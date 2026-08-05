"""Cifrado de las credenciales del portal antes de guardarlas.

AES-256-GCM, formato `base64(iv ‖ tag ‖ ciphertext)`. La clave la comparten el
provisioner (que cifra) y el backend web (que descifra para mostrar); ninguno de
los dos ve los salts, que quedan solo acá.

## El orden de los campos no es libre

`iv ‖ tag ‖ ct` es la convención que el backend ya usa para los códigos RF de los
controles (`backend-nestjs/src/common/gcm.ts`). La librería de Python devuelve
`ct ‖ tag` pegados, así que hay que separarlos a mano — es exactamente el tipo de
detalle que, si se deja como viene, hace que un lado no pueda leer al otro y se
manifieste como "el descifrado falla" sin decir por qué.

## Por qué cifra el provisioner y no la web

Si las passwords viajaran en claro por `gtd.provisioning_queue`, quedarían en
claro en la tabla y —lo que no se puede borrar después— en el WAL de Postgres.
Cifrando de este lado nunca existen en claro fuera de la memoria del proceso.

## Por qué GCM y no algo más simple

Es AEAD: si alguien altera un ciphertext en la base, el descifrado FALLA en vez
de devolver 6 caracteres de basura que se imprimirían en una etiqueta como si
fueran una password.
"""

from __future__ import annotations

import base64
import os

# 96 bits es el tamaño de nonce que GCM usa nativamente; con otro tamaño la
# especificación obliga a un paso extra de derivación y no gana nada.
NONCE_BYTES = 12
TAG_BYTES = 16
CLAVE_BYTES = 32          # AES-256


class ClaveInvalida(RuntimeError):
    """La clave de cifrado no está o no mide lo que tiene que medir."""


def cargar_clave(valor: str) -> bytes:
    """La clave viene en base64 por entorno. 32 bytes exactos o nada."""
    if not valor:
        raise ClaveInvalida(
            "Falta GTD_CRED_KEY: sin ella no se pueden guardar las "
            "credenciales del portal.",
        )
    try:
        crudos = base64.b64decode(valor, validate=True)
    except Exception as e:  # noqa: BLE001
        raise ClaveInvalida(f"GTD_CRED_KEY no es base64 válido: {e}") from e

    if len(crudos) != CLAVE_BYTES:
        raise ClaveInvalida(
            f"GTD_CRED_KEY tiene {len(crudos)} bytes y AES-256 necesita "
            f"{CLAVE_BYTES}. Generá una con: openssl rand -base64 32",
        )
    return crudos


def cifrar(clave: bytes, texto: str) -> str:
    """`base64(iv ‖ tag ‖ ct)`. IV nuevo por llamada, siempre: reusarlo en GCM
    rompe el esquema entero."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = os.urandom(NONCE_BYTES)
    # La librería devuelve ct ‖ tag pegados; el backend espera el tag adelante.
    sellado = AESGCM(clave).encrypt(iv, texto.encode(), None)
    ct, tag = sellado[:-TAG_BYTES], sellado[-TAG_BYTES:]
    return base64.b64encode(iv + tag + ct).decode()


def descifrar(clave: bytes, blob: str) -> str:
    """Inversa de `cifrar`. Levanta si el blob fue alterado."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    crudos = base64.b64decode(blob, validate=True)
    iv = crudos[:NONCE_BYTES]
    tag = crudos[NONCE_BYTES:NONCE_BYTES + TAG_BYTES]
    ct = crudos[NONCE_BYTES + TAG_BYTES:]
    return AESGCM(clave).decrypt(iv, ct + tag, None).decode()
