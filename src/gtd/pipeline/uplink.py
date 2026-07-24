"""Uplink: mensaje MQTT entrante → dominio → repo.

MQTT (av/+/{status,tele,up}) → parseo → ruteo por canal → escritura.
El NOTIFY al backend de app lo dispara el trigger de Postgres al escribir
panel_state (no este código).

Un payload malformado se CUENTA y se DESCARTA: nunca tira abajo el pipeline.
"""

from __future__ import annotations

import logging

from ..db.repo import Repo
from ..domain import payloads
from ..domain.contract import AlarmaOrigin, Channel, UpType
from ..mqtt import topics

log = logging.getLogger("gtd.uplink")


async def handle(raw_topic: str, raw_payload: bytes, repo: Repo) -> None:
    parsed = topics.parse(raw_topic)
    if parsed is None:
        log.debug("tópico ignorado: %s", raw_topic)
        return
    device_id, channel = parsed

    try:
        model, doc = payloads.parse(channel, raw_payload)
    except payloads.PayloadError as e:
        # TODO(obs): contador in_drops por (device_id, channel)
        log.warning("payload descartado mac=%s canal=%s: %s", device_id, channel.value, e)
        return

    if channel is Channel.STATUS:
        await repo.upsert_panel_state(
            device_id, online=model.online, modo_energia=model.modo,
            last_seen=model.ts,
        )

    elif channel is Channel.TELE:
        await repo.upsert_panel_state(
            device_id, modo_energia=model.modo_energia, alarma_mode=model.alarma_mode,
            cfg_v=model.cfg_v, rf_gen=model.rf_gen, energia=model.energia,
            last_seen=model.ts,
        )

    elif channel is Channel.UP:
        await _handle_up(device_id, doc, model, repo)


async def _handle_up(device_id, doc, model, repo: Repo) -> None:
    t = doc.get("t")

    if t == UpType.ALARMA.value:
        # Idempotencia por eid; guardado del evento crudo.
        await repo.insert_evento(device_id, t, doc, eid=model.eid, ts=model.ts)
        # Correlación: si la alarma vino por MQTT trae el cid del comando.
        if model.origin is AlarmaOrigin.MQTT and model.cid:
            await repo.confirm_command(model.cid, res="ok",
                                       det=f"alarma {model.mode.value}")

    elif t == UpType.ACK.value:
        # Ack de cmd (cid) o de cfg (cfg_v). Cierra el ciclo del downlink.
        if model.cid:
            await repo.confirm_command(model.cid, res=model.res, det=model.det)
        await repo.insert_evento(device_id, t, doc, ts=model.ts)

    else:  # scan | ota
        await repo.insert_evento(device_id, t, doc, ts=model.ts)
