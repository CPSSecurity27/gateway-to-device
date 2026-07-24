"""Parseo de payloads entrantes (JSON crudo → modelo validado).

Puro: bytes/str → (modelo, doc). Sin estado, sin I/O. La política (dedup,
escritura, notify) la aplican los pipelines.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .contract import Channel, UpType
from .models import StatusMsg, TeleMsg, UpAck, UpAlarma, UpOta, UpScan, is_schema_ok


class PayloadError(ValueError):
    """Payload malformado / esquema incompatible. Se cuenta y se descarta —
    NUNCA tira abajo el pipeline (un panel con firmware raro no puede colgar
    al GtD)."""


# up: mapa discriminador "t" → modelo
_UP_MODELS = {
    UpType.ALARMA.value: UpAlarma,
    UpType.ACK.value: UpAck,
    UpType.SCAN.value: UpScan,
    UpType.OTA.value: UpOta,
}


def load_json(raw: bytes | str) -> dict[str, Any]:
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise PayloadError(f"JSON inválido: {e}") from e
    if not isinstance(doc, dict):
        raise PayloadError("payload no es un objeto JSON")
    if not is_schema_ok(doc):
        raise PayloadError(f"esquema incompatible: v={doc.get('v')!r}")
    return doc


def parse(channel: Channel, raw: bytes | str):
    """Devuelve (modelo, doc_crudo). El doc crudo va tal cual a Postgres (JSONB)."""
    doc = load_json(raw)
    try:
        if channel is Channel.STATUS:
            return StatusMsg.model_validate(doc), doc
        if channel is Channel.TELE:
            return TeleMsg.model_validate(doc), doc
        if channel is Channel.UP:
            t = doc.get("t")
            model_cls = _UP_MODELS.get(t)
            if model_cls is None:
                raise PayloadError(f"up con t desconocido: {t!r}")
            return model_cls.model_validate(doc), doc
    except ValidationError as e:
        raise PayloadError(f"validación falló en {channel.value}: {e}") from e

    raise PayloadError(f"canal no parseable como subida: {channel!r}")
