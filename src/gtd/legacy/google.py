"""Token OAuth2 de una service account de Google, async y sin dependencias.

Por qué a mano y no con `firebase-admin` o `google-auth`:

- **`firebase-admin` es SÍNCRONO** y arrastra `grpcio`. Este proceso atiende
  MQTT en un event loop; una llamada bloqueante a Firebase adentro de ese loop
  frena la recepción de activaciones. Y `grpcio` en una Pi de 921 MB es peso
  que no hace falta pagar por lo que en el fondo son dos llamadas REST.
- **`google-auth`** resuelve solo esto —firmar un JWT y canjearlo— pero sus
  transportes son `requests` o `urllib3`, los dos síncronos, así que habría que
  sumar otra dependencia igual y seguir bloqueando.

Lo que hace Google acá es simple y está documentado (OAuth 2.0 for Service
Accounts): se firma un JWT con la clave privada de la cuenta y se canjea por un
access token. Son 30 líneas con `cryptography`, que ya es dependencia del repo.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

log = logging.getLogger("gtd.legacy.google")

# Margen para no usar un token que vence mientras viaja el request.
MARGEN_S = 120


def _b64(raw: bytes) -> str:
    """base64url sin padding, que es lo que pide JWT."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class CuentaDeServicio:
    """Cachea el access token y lo renueva cuando está por vencer."""

    def __init__(self, ruta: str | Path, scopes: list[str]) -> None:
        doc = json.loads(Path(ruta).read_text(encoding="utf-8"))
        faltan = [k for k in ("client_email", "private_key", "token_uri")
                  if not doc.get(k)]
        if faltan:
            raise ValueError(f"service account incompleta, faltan: {faltan}")

        self.project_id: str = doc.get("project_id", "")
        self._email: str = doc["client_email"]
        self._token_uri: str = doc["token_uri"]
        self._scopes = " ".join(scopes)
        self._key = serialization.load_pem_private_key(
            doc["private_key"].encode("utf-8"), password=None,
        )
        self._token: str = ""
        self._vence: float = 0.0

    def _assertion(self) -> str:
        ahora = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": self._email,
            "scope": self._scopes,
            "aud": self._token_uri,
            "iat": ahora,
            "exp": ahora + 3600,
        }
        firmable = ".".join(
            _b64(json.dumps(p, separators=(",", ":")).encode("utf-8"))
            for p in (header, claims)
        ).encode("ascii")
        firma = self._key.sign(firmable, padding.PKCS1v15(), hashes.SHA256())
        return f"{firmable.decode('ascii')}.{_b64(firma)}"

    async def token(self, cliente: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._vence - MARGEN_S:
            return self._token

        r = await cliente.post(
            self._token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self._assertion(),
            },
            timeout=20.0,
        )
        r.raise_for_status()
        doc = r.json()
        self._token = doc["access_token"]
        self._vence = time.time() + int(doc.get("expires_in", 3600))
        log.debug("token renovado (vence en %ss)", doc.get("expires_in"))
        return self._token

    async def headers(self, cliente: httpx.AsyncClient) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.token(cliente)}"}
