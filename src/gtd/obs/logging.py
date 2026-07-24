"""Logging + redacción de secretos.

REGLA: nunca una password (WiFi, broker) ni un device secret en un log. La
config baja con secretos; si algo del downlink se loguea, pasa por redact().
"""

from __future__ import annotations

import logging
from typing import Any

_SECRET_KEYS = {"password", "psw", "pass", "secret", "mqtt_password"}


def setup(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
    )


def redact(obj: Any) -> Any:
    """Devuelve una copia con los valores sensibles enmascarados. Para loguear
    documentos de config sin filtrar passwords."""
    if isinstance(obj, dict):
        return {
            k: ("***" if k.lower() in _SECRET_KEYS else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj
