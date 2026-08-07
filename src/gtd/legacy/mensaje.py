"""El mensaje que publica la app VIEJA en `cliente/servidor`.

Formato fijo: es un APK ya distribuido y no se puede cambiar. Sale de
`lib/main.dart` del repo de la app (commit acf2f55, v4.0.0+4), función
`_sendMqttInternal`:

    {"cliente_id": "44679351",
     "modo_a": "cps003",
     "gps": {"longitud": "-64.865000", "latitud": "-24.233000"}}

Tres cosas del original que hay que respetar tal cual son:

- **`gps` puede no venir.** Si no hay fix en 2 s la app publica sin el campo
  (`_getBestPositionQuick` devuelve null y el `if` lo omite). No es un error.
- **Las coordenadas viajan como STRING**, con 6 decimales
  (`toStringAsFixed(6)`). Y el orden de las claves es longitud primero.
- **`cliente_id` es el DNI como string.** El bridge viejo exigía más de 4
  dígitos (`cliente_id_valido`) y descartaba el resto.

Esto NO valida el dominio: no sabe si el DNI existe ni si el modo es real. Eso
lo decide `gtd.enqueue_legacy_alarm` adentro de la base, en un solo lugar. Acá
solo se convierte JSON crudo en algo tipado, o se rechaza por malformado.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


class MensajeInvalido(ValueError):
    """El payload no tiene la forma mínima esperada."""


@dataclass(frozen=True)
class Activacion:
    dni: str
    modo: str                    # cps001..cps007, cps999 — sin validar
    lat: float | None = None
    lng: float | None = None

    @property
    def es_desactivacion(self) -> bool:
        return self.modo == "cps999"


def _coord(valor: object) -> float | None:
    """La app manda las coordenadas como string. Un valor ilegible NO invalida
    el mensaje entero: se activa igual, sin GPS. Preferimos una alarma sin
    ubicación antes que ninguna alarma."""
    if valor is None:
        return None
    try:
        n = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None
    # 0,0 es el "sin fix" clásico, no un punto en el golfo de Guinea.
    return None if n == 0.0 else n


def parse(payload: bytes | str) -> Activacion:
    try:
        doc = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as e:
        raise MensajeInvalido(f"JSON inválido: {e}") from e

    if not isinstance(doc, dict):
        raise MensajeInvalido("el payload no es un objeto JSON")

    dni = str(doc.get("cliente_id") or "").strip()
    modo = str(doc.get("modo_a") or "").strip().lower()
    if not dni:
        raise MensajeInvalido("sin cliente_id")
    if not modo:
        raise MensajeInvalido("sin modo_a")

    gps = doc.get("gps")
    lat = lng = None
    if isinstance(gps, dict):
        lat = _coord(gps.get("latitud", gps.get("Latitud")))
        lng = _coord(gps.get("longitud", gps.get("Longitud")))
    # Media coordenada no es una posición.
    if lat is None or lng is None:
        lat = lng = None

    return Activacion(dni=dni, modo=modo, lat=lat, lng=lng)
