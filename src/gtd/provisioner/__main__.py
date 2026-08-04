"""Provisioner: da de alta y de baja credenciales de panel en el broker.

    python -m gtd.provisioner

Proceso APARTE del GtD y con privilegios propios. El GtD está encerrado
(`NoNewPrivileges`, `ProtectSystem=strict`) porque recibe payloads de cada
panel; esto necesita escribir /etc/mosquitto y recargar el servicio. Compartimos
el repo —la derivación HMAC tiene que coincidir con el firmware— pero no el
proceso.

No habla MQTT. Su única entrada son filas de una base local.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..obs import logging as obs
from ..settings import Settings
from .broker import Registrador
from .cola import Cola, ColaPg, ColaStub
from .servicio import BARRIDO_S, bucle

log = logging.getLogger("gtd.provisioner")


async def run() -> None:
    settings = Settings()
    obs.setup(settings.log_level)

    script = Path(settings.provisioner_script)
    if not script.is_file():
        raise SystemExit(f"No existe el script de provisioning: {script}")

    if not settings.salt_mqtt and not settings.panel_password:
        log.warning(
            "Sin GTD_SALT_MQTT ni GTD_PANEL_PASSWORD: el script va a pedir el "
            "salt por consola y no hay nadie para tipearlo. Todo va a fallar.",
        )

    cola: Cola
    if settings.pg_dsn:
        cola = ColaPg(settings.pg_dsn)
    else:
        log.warning("Sin GTD_PG_DSN: no hay cola que drenar.")
        cola = ColaStub()

    registrador = Registrador(script, settings.salt_mqtt, settings.panel_password)

    await cola.start()
    try:
        await bucle(cola, registrador, BARRIDO_S)
    finally:
        await cola.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Provisioner detenido.")


if __name__ == "__main__":
    main()
