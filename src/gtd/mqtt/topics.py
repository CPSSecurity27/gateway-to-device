"""Parseo y armado de tópicos av/<id>/<canal>.

Este módulo ES el borde donde se traduce la identidad (doc 06 §1): de acá para
adentro (presencia, repo, logs) todo habla MAC PELADA (12 hex mayúsculas); el
prefijo AV- existe solo en MQTT y se repone recién al publicar.
"""

from __future__ import annotations

from ..domain.contract import (
    TOPIC_ROOT,
    Channel,
    device_id_from_mac,
    mac_from_device_id,
)

# Canales que suben (nos suscribimos a estos). cfg/cmd son de bajada: los publica
# el GtD, no los recibe.
UPLINK_CHANNELS = (Channel.STATUS, Channel.TELE, Channel.UP)


def subscriptions() -> list[str]:
    """Suscripciones del GtD: av/+/{status,tele,up}."""
    return [f"{TOPIC_ROOT}/+/{ch.value}" for ch in UPLINK_CHANNELS]


def parse(topic: str) -> tuple[str, Channel] | None:
    """av/<id>/<canal> → (MAC PELADA, Channel). None si no matchea/no es subida.

    Un id que no valida (sin AV-, hex corrupto) se descarta acá: antes entraba
    crudo y rompía silenciosamente aguas abajo — todas las llamadas al repo
    daban unknown_device sin que nadie lo note.
    """
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != TOPIC_ROOT:
        return None
    mac = mac_from_device_id(parts[1])
    if mac is None:
        return None
    try:
        channel = Channel(parts[2])
    except ValueError:
        return None
    if channel not in UPLINK_CHANNELS:
        return None
    return mac, channel


def cmd_topic(mac: str) -> str:
    return f"{TOPIC_ROOT}/{device_id_from_mac(mac)}/{Channel.CMD.value}"


def cfg_topic(mac: str) -> str:
    return f"{TOPIC_ROOT}/{device_id_from_mac(mac)}/{Channel.CFG.value}"
