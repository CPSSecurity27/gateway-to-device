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

# Último `estado` visto por MAC, para loguear solo las TRANSICIONES: el status es
# retained y se repite en cada reconexión, no queremos una línea por repetición.
# Acotado por el tamaño de la flota; se pierde al reiniciar (y entonces el primer
# status de cada panel se loguea de nuevo, que es justo lo que se quiere al arrancar).
_last_estado: dict[str, str] = {}

# estado → (nivel, marca). offline en WARNING para que salte en el journal.
# Marcas ASCII a propósito: el log puede terminar en una consola cp1252 (Windows),
# donde un carácter no-latin1 se escapa o revienta al emitir.
_ESTADO_LOG = {
    "online": (logging.INFO, "[+] panel ONLINE"),
    "durmiendo": (logging.INFO, "[~] panel DURMIENDO"),
    "offline": (logging.WARNING, "[-] panel OFFLINE"),
}


def _log_transicion(device_id: str, model) -> None:
    """Loguea el cambio de estado de un panel. Silencioso si no cambió."""
    estado = model.estado
    if _last_estado.get(device_id) == estado:
        return
    anterior = _last_estado.get(device_id, "?")
    _last_estado[device_id] = estado

    nivel, marca = _ESTADO_LOG.get(estado, (logging.INFO, f"? panel {estado}"))
    detalle = ""
    if estado == "online" and model.modo:
        detalle = f" modo={model.modo}"
    elif estado == "durmiendo" and model.despierta:
        detalle = f" despierta={model.despierta}"
    elif estado == "offline" and model.causa:
        detalle = f" causa={model.causa}"
    if model.fw:
        detalle += f" fw={model.fw}"

    log.log(nivel, "%s mac=%s%s (antes=%s)", marca, device_id, detalle, anterior)


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
        _log_transicion(device_id, model)
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
