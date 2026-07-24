"""Parseo y armado de tópicos av/<id>/<canal>."""

from __future__ import annotations

from ..domain.contract import TOPIC_ROOT, Channel

# Canales que suben (nos suscribimos a estos). cfg/cmd son de bajada: los publica
# el GtD, no los recibe.
UPLINK_CHANNELS = (Channel.STATUS, Channel.TELE, Channel.UP)


def subscriptions() -> list[str]:
    """Suscripciones del GtD: av/+/{status,tele,up}."""
    return [f"{TOPIC_ROOT}/+/{ch.value}" for ch in UPLINK_CHANNELS]


def parse(topic: str) -> tuple[str, Channel] | None:
    """av/<id>/<canal> → (device_id, Channel). None si no matchea/no es subida."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != TOPIC_ROOT:
        return None
    device_id, raw_channel = parts[1], parts[2]
    try:
        channel = Channel(raw_channel)
    except ValueError:
        return None
    if channel not in UPLINK_CHANNELS:
        return None
    return device_id, channel


def cmd_topic(device_id: str) -> str:
    return f"{TOPIC_ROOT}/{device_id}/{Channel.CMD.value}"


def cfg_topic(device_id: str) -> str:
    return f"{TOPIC_ROOT}/{device_id}/{Channel.CFG.value}"
