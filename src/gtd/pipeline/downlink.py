"""Downlink: NOTIFY de Postgres → publica cmd/cfg en MQTT.

El backend de app INSERTA un comando/config 'pending' en Postgres → trigger
NOTIFY → el GtD lo lee y lo publica. La CONFIRMACIÓN no llega acá: vuelve por el
uplink (up t:ack / up t:alarma con el mismo cid), que marca 'confirmed'.

`publish` es el cliente aiomqtt (se le pasa desde el bucle de servicio).
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from ..db.listener import CH_COMMANDS, CH_CONFIG
from ..db.repo import Repo
from ..domain.contract import MQTT_IN_PAYLOAD_MAX
from ..mqtt import topics

log = logging.getLogger("gtd.downlink")


class Publisher(Protocol):
    async def publish(self, topic: str, payload: bytes | str, qos: int = 0,
                      retain: bool = False) -> None: ...


def _compacto(doc) -> str:
    """JSON sin espacios: cada byte cuenta contra MQTT_IN_PAYLOAD_MAX."""
    return json.dumps(doc, separators=(",", ":"))


async def handle(channel: str, mac: str, repo: Repo, pub: Publisher) -> None:
    if channel == CH_COMMANDS:
        for cmd in await repo.fetch_pending_commands(mac):
            await pub.publish(topics.cmd_topic(mac),
                              _compacto(cmd["payload"]), qos=1)
            await repo.mark_command_sent(cmd["cid"])
            log.info("cmd publicado mac=%s cid=%s tipo=%s",
                     mac, cmd.get("cid"), cmd.get("tipo"))

    elif channel == CH_CONFIG:
        cfg = await repo.fetch_pending_config(mac)
        if cfg is not None:
            raw = _compacto(cfg["payload"])
            crudo = raw.encode()
            if len(crudo) > MQTT_IN_PAYLOAD_MAX:
                # Publicarla sería un no-op SILENCIOSO del panel (ni ack de
                # error); mark_sent sería mentira. failed + detalle es el único
                # camino honesto (P0-2) — y corta el loop de NOTIFY.
                det = f"payload {len(crudo)} B > {MQTT_IN_PAYLOAD_MAX}"
                log.error("cfg NO publicada mac=%s cfg_v=%s: %s",
                          mac, cfg["cfg_v"], det)
                await repo.mark_config_failed(mac, cfg["cfg_v"], det)
                return
            # cfg va RETAINED: es estado deseado, el panel lo toma al conectar.
            await pub.publish(topics.cfg_topic(mac), raw, qos=1, retain=True)
            await repo.mark_config_sent(mac, cfg["cfg_v"])
            log.info("cfg publicada mac=%s cfg_v=%s (%d B)",
                     mac, cfg.get("cfg_v"), len(crudo))

    else:
        log.warning("NOTIFY de canal desconocido: %s", channel)
