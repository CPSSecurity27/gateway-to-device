"""Cliente REST de la Realtime Database, async.

La app vieja lee `cpssecurityapp` y no se puede cambiar: esto escribe lo que ella
espera encontrar, donde lo espera encontrar. Ver `docs/09-app-vieja.md`.

Dos cosas del formato que salieron de mirar la base real (2026-08-07) y que no
se deducen de ningún doc:

- **La app muestra los valores CRUDOS**, sin traducir. Lo que se escriba en
  `modoalarma` es literalmente lo que ve el vecino en el teléfono.
- **Los valores existentes están entrecomillados** (`"\\"Mza 17-B casa 24\\""`),
  pero `_clean()` de la app borra todas las comillas antes de mostrar. Así que
  acá se escribe LIMPIO: es lo mismo para la app y es legible en la consola de
  Firebase.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .google import CuentaDeServicio

log = logging.getLogger("gtd.legacy.rtdb")

SCOPES = [
    "https://www.googleapis.com/auth/firebase.database",
    "https://www.googleapis.com/auth/userinfo.email",
]


class Rtdb:
    def __init__(
        self,
        base_url: str,
        cuenta: CuentaDeServicio,
        cliente: httpx.AsyncClient,
        prefijo: str = "",
    ) -> None:
        self._base = base_url.rstrip("/")
        self._cuenta = cuenta
        self._http = cliente
        # Prefijo de scratch para ensayar sin que los teléfonos de los vecinos
        # vean una activación falsa: la app tiene listeners abiertos sobre los
        # paths reales. Vacío = producción.
        self._prefijo = prefijo.strip("/")

    def _url(self, path: str) -> str:
        p = path.strip("/")
        if self._prefijo:
            p = f"{self._prefijo}/{p}"
        return f"{self._base}/{p}.json"

    async def patch(self, path: str, doc: dict[str, Any]) -> None:
        """PATCH = merge. No pisa las claves que no vienen en `doc`."""
        r = await self._http.patch(
            self._url(path), json=doc,
            headers=await self._cuenta.headers(self._http), timeout=20.0,
        )
        r.raise_for_status()

    async def put(self, path: str, doc: Any) -> None:
        """PUT = reemplaza el nodo entero."""
        r = await self._http.put(
            self._url(path), json=doc,
            headers=await self._cuenta.headers(self._http), timeout=20.0,
        )
        r.raise_for_status()

    async def delete(self, path: str) -> None:
        r = await self._http.delete(
            self._url(path),
            headers=await self._cuenta.headers(self._http), timeout=20.0,
        )
        r.raise_for_status()

    async def get(self, path: str, **params: str) -> Any:
        r = await self._http.get(
            self._url(path), params=params or None,
            headers=await self._cuenta.headers(self._http), timeout=30.0,
        )
        r.raise_for_status()
        return r.json()
