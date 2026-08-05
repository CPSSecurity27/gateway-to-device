"""Uplink: mensaje MQTT entrante → dominio → repo.

MQTT (av/+/{status,tele,up}) → parseo → ruteo por canal → escritura.
El NOTIFY al backend de app lo dispara el trigger de Postgres al escribir
panel_state (no este código).

Un payload malformado se CUENTA y se DESCARTA: nunca tira abajo el pipeline.
"""

from __future__ import annotations

import json
import logging

from ..db.repo import Repo, RepoUnavailable
from ..db.spool import Spool
from ..domain import payloads
from ..domain.contract import AlarmaOrigin, Channel, UpType
from ..mqtt import topics
from ..obs.logging import redact
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


def _log_status(mac: str, model, *, silencio: float, gap: float) -> None:
    """Loguea lo que el `status` significa: transición, reconexión o nada."""
    evento, anterior = presencia.ver_status(
        mac, model.estado, silencio=silencio, gap=gap,
        despierta=model.despierta,
    )

    if evento is presencia.EventoStatus.REPETIDO:
        return

    if evento is presencia.EventoStatus.RECONEXION:
        # El broker no publica el LWT en un takeover de sesión: sin esto, una
        # reconexión es invisible y el panel parece haber estado online todo el
        # tiempo. Ver presencia.py.
        log.info("[r] panel RECONECTÓ mac=%s%s (tras %.0fs sin hablar, van %d)",
                 mac, _detalle(model), silencio,
                 presencia.reconexiones(mac))
        return

    nivel, marca = _ESTADO_LOG.get(model.estado, (logging.INFO, f"? panel {model.estado}"))
    log.log(nivel, "%s mac=%s%s (antes=%s)", marca, mac, _detalle(model), anterior)


async def handle(raw_topic: str, raw_payload: bytes, repo: Repo,
                 *, gap_reconexion: float = 60.0,
                 spool: Spool | None = None) -> None:
    parsed = topics.parse(raw_topic)
    if parsed is None:
        log.debug("tópico ignorado: %s", raw_topic)
        return
    mac, channel = parsed   # MAC pelada: topics.parse ya validó y tradujo

    # Se registra ANTES de validar el payload: que el mensaje esté mal formado no
    # significa que el panel esté muerto — está vivo y hablando, que es lo que
    # mide la presencia.
    act = presencia.actividad(mac)
    if act.volvio:
        log.warning("[+] panel VOLVIÓ mac=%s (estaba sin señal)", mac)
        await repo.upsert_panel_state(mac, estado="online")

    try:
        model, doc = payloads.parse(channel, raw_payload)
    except payloads.PayloadError as e:
        # TODO(obs): contador in_drops por (mac, channel)
        log.warning("payload descartado mac=%s canal=%s: %s", mac, channel.value, e)
        return

    if channel is Channel.STATUS:
        _log_status(mac, model, silencio=act.silencio, gap=gap_reconexion)
        # El estado viaja COMPLETO (P1-4): 'durmiendo' no es 'offline'. El
        # last_seen NO viaja — lo pone el servidor (P1-3); el reloj del panel
        # va aparte (ts + tsq), que es el único modo de interpretarlo.
        await repo.upsert_panel_state(
            mac, estado=model.estado, modo_energia=model.modo, fw=model.fw,
            despierta=model.despierta, ts=model.ts, tsq=model.tsq,
        )

    elif channel is Channel.TELE:
        # `red` va aparte del resto del snapshot: ssid, ip y rssi tienen columna
        # propia porque son preguntas de FLOTA ("¿cuáles tienen mala señal?"),
        # y eso no se puede responder leyendo un JSONB fila por fila.
        await repo.upsert_panel_state(
            mac, modo_energia=model.modo_energia, alarma_mode=model.alarma_mode,
            cfg_v=model.cfg_v, rf_gen=model.rf_gen, energia=model.energia,
            red=model.red, tele=model.snapshot,
            ts=model.ts, tsq=model.tsq,
        )

    elif channel is Channel.UP:
        try:
            await _handle_up(mac, doc, model, repo)
        except RepoUnavailable:
            if spool is None:
                raise
            # El PUBACK ya salió: este doc no existe en ningún otro lado.
            spool.append({"mac": mac, "doc": doc})
            log.error("base caída: up de mac=%s al spool", mac)


async def replay(mac: str, doc: dict, repo: Repo) -> None:
    """Reinserta un `up` guardado en el spool. Idempotente: el dedup por eid
    hace que reinsertar dos veces devuelva False y nada más."""
    model, doc = payloads.parse(Channel.UP, json.dumps(doc))
    await _handle_up(mac, doc, model, repo)


async def _handle_up(mac, doc, model, repo: Repo) -> None:
    t = doc.get("t")

    if t == UpType.ALARMA.value:
        # Idempotencia por eid; guardado del evento crudo. El bool ES el dedup:
        # en una redistribución QoS 1, confirmar el comando dos veces pisaría
        # el detalle de la confirmación real (doc 06 §3.c).
        nuevo = await repo.insert_evento(mac, t, doc, eid=model.eid, ts=model.ts)
        if not nuevo:
            log.info("alarma duplicada (QoS1) mac=%s eid=%s — no se reconfirma",
                     mac, model.eid)
        elif model.origin is AlarmaOrigin.MQTT and model.cid:
            # Correlación: si la alarma vino por MQTT trae el cid del comando.
            await repo.confirm_command(model.cid, res="ok",
                                       det=f"alarma {model.mode.value}")

    elif t == UpType.CFG_FULL.value:
        # El panel espeja su config al conectar. Se guarda como espejo (arbitrado
        # por cfg_v en el repo), NO como evento: no es algo que pasó, es estado.
        # El doc lleva las passwords WiFi en redes[].psw — jamás loguearlo entero.
        await repo.upsert_config_espejo(mac, model.cfg_v, doc)
        # Histórico de cómo cambió la config, SIN passwords: el claro ya vive en
        # el espejo; no se duplica en una tabla append-only (P2-7).
        await repo.insert_evento(mac, t, redact(doc), ts=model.ts)

    elif t == UpType.ACK.value:
        # Ack de cmd (trae cid) o de cfg (trae cfg_v y NO trae cid). Son dos
        # caminos distintos: el de cfg se correlaciona por (mac, cfg_v), y sin
        # esta rama caía en el dead letter como `sin_destino` — la confirmación
        # existía y la tirábamos.
        if model.cid:
            await repo.confirm_command(model.cid, res=model.res, det=model.det)
        elif model.cfg_v is not None:
            await repo.confirm_config(mac, model.cfg_v,
                                      res=model.res or "ok", det=model.det)
        await repo.insert_evento(mac, t, doc, ts=model.ts)

    else:  # scan | ota | rf_rx | rf_rx_end | audit | audit_detalle
        await repo.insert_evento(mac, t, doc, ts=model.ts)
