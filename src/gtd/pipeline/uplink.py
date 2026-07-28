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
from . import presencia

log = logging.getLogger("gtd.uplink")

# estado → (nivel, marca). offline en WARNING para que salte en el journal.
# Marcas ASCII a propósito: el log puede terminar en una consola cp1252 (Windows),
# donde un carácter no-latin1 se escapa o revienta al emitir.
_ESTADO_LOG = {
    "online": (logging.INFO, "[+] panel ONLINE"),
    "durmiendo": (logging.INFO, "[~] panel DURMIENDO"),
    "offline": (logging.WARNING, "[-] panel OFFLINE"),
}


def _detalle(model) -> str:
    detalle = ""
    if model.estado == "online" and model.modo:
        detalle = f" modo={model.modo}"
    elif model.estado == "durmiendo" and model.despierta:
        detalle = f" despierta={model.despierta}"
    elif model.estado == "offline" and model.causa:
        detalle = f" causa={model.causa}"
    if model.fw:
        detalle += f" fw={model.fw}"
    return detalle


def _log_status(device_id: str, model, *, silencio: float, gap: float) -> None:
    """Loguea lo que el `status` significa: transición, reconexión o nada."""
    evento, anterior = presencia.ver_status(
        device_id, model.estado, silencio=silencio, gap=gap,
        despierta=model.despierta,
    )

    if evento is presencia.EventoStatus.REPETIDO:
        return

    if evento is presencia.EventoStatus.RECONEXION:
        # El broker no publica el LWT en un takeover de sesión: sin esto, una
        # reconexión es invisible y el panel parece haber estado online todo el
        # tiempo. Ver presencia.py.
        log.info("[r] panel RECONECTÓ mac=%s%s (tras %.0fs sin hablar, van %d)",
                 device_id, _detalle(model), silencio,
                 presencia.reconexiones(device_id))
        return

    nivel, marca = _ESTADO_LOG.get(model.estado, (logging.INFO, f"? panel {model.estado}"))
    log.log(nivel, "%s mac=%s%s (antes=%s)", marca, device_id, _detalle(model), anterior)


async def handle(raw_topic: str, raw_payload: bytes, repo: Repo,
                 *, gap_reconexion: float = 60.0) -> None:
    parsed = topics.parse(raw_topic)
    if parsed is None:
        log.debug("tópico ignorado: %s", raw_topic)
        return
    device_id, channel = parsed

    # Se registra ANTES de validar el payload: que el mensaje esté mal formado no
    # significa que el panel esté muerto — está vivo y hablando, que es lo que
    # mide la presencia.
    act = presencia.actividad(device_id)
    if act.volvio:
        log.warning("[+] panel VOLVIÓ mac=%s (estaba sin señal)", device_id)
        await repo.upsert_panel_state(device_id, online=True)

    try:
        model, doc = payloads.parse(channel, raw_payload)
    except payloads.PayloadError as e:
        # TODO(obs): contador in_drops por (device_id, channel)
        log.warning("payload descartado mac=%s canal=%s: %s", device_id, channel.value, e)
        return

    if channel is Channel.STATUS:
        _log_status(device_id, model, silencio=act.silencio, gap=gap_reconexion)
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

    elif t == UpType.CFG_FULL.value:
        # El panel espeja su config al conectar. Se guarda como espejo (arbitrado
        # por cfg_v en el repo), NO como evento: no es algo que pasó, es estado.
        # El doc lleva las passwords WiFi en redes[].psw — jamás loguearlo entero.
        await repo.upsert_config_espejo(device_id, model.cfg_v, doc)

    elif t == UpType.ACK.value:
        # Ack de cmd (cid) o de cfg (cfg_v). Cierra el ciclo del downlink.
        if model.cid:
            await repo.confirm_command(model.cid, res=model.res, det=model.det)
        await repo.insert_evento(device_id, t, doc, ts=model.ts)

    else:  # scan | ota
        await repo.insert_evento(device_id, t, doc, ts=model.ts)
