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
from .broker import Registrador, RegistradorFalso
from .cola import Cola, ColaPg, ColaStub
from .fabrica import Fabricador
from .huerfanos import barrer
from .portal import SaltInvalido
from .servicio import BARRIDO_S, bucle

log = logging.getLogger("gtd.provisioner")


async def run() -> None:
    settings = Settings()
    obs.setup(settings.log_level)

    script = Path(settings.provisioner_script)
    if not settings.registrador_falso:
        if not script.is_file():
            raise SystemExit(f"No existe el script de provisioning: {script}")

        if not settings.salt_mqtt and not settings.panel_password:
            log.warning(
                "Sin GTD_SALT_MQTT ni GTD_PANEL_PASSWORD: el script va a pedir "
                "el salt por consola y no hay nadie para tipearlo. Todo va a "
                "fallar.",
            )

    # El fabricador se arma ACÁ y no en cada equipo: valida los salts contra el
    # vector de verificación una sola vez, al arrancar. Si están mal, el
    # servicio no levanta — mejor que descubrirlo con la etiqueta ya pegada.
    fabricador = None
    if settings.salt_tec and settings.salt_cps and settings.cred_key:
        try:
            fabricador = Fabricador(
                settings.salt_tec, settings.salt_cps, settings.cred_key,
            )
        except SaltInvalido as e:
            raise SystemExit(str(e)) from e
    else:
        # No es fatal: `provision` y `revoke` siguen andando. Lo que no se va a
        # poder es fabricar, y el alta de la web va a fallar con ese motivo.
        log.warning(
            "Sin GTD_SALT_TEC / GTD_SALT_CPS / GTD_CRED_KEY: no se pueden "
            "derivar las credenciales del portal. Las altas de fábrica van a "
            "fallar; provision y revoke siguen funcionando.",
        )

    cola: Cola
    dsn = settings.dsn_del_provisioner
    if dsn:
        # OJO: es el DSN del PROVISIONER, no el del GtD. Van con usuarios
        # distintos a propósito — ver `provisioner_dsn` en settings.py.
        cola = ColaPg(dsn)
    else:
        log.warning("Sin GTD_PROVISIONER_DSN ni GTD_PG_DSN: no hay cola que drenar.")
        cola = ColaStub()

    registrador: object
    if settings.registrador_falso:
        # Grita, no susurra: un provisioner que dice "ok" sin haber tocado el
        # broker es exactamente lo que no querés que pase inadvertido.
        log.warning(
            "=== GTD_REGISTRADOR_FALSO ACTIVO — no se toca Mosquitto. "
            "Las credenciales del portal SÍ se derivan de verdad; el registro "
            "en el broker se simula. NO usar en producción. ===",
        )
        registrador = RegistradorFalso()
    else:
        registrador = Registrador(
            script, settings.salt_mqtt, settings.panel_password,
        )

    await cola.start()
    try:
        if settings.barrer_huerfanos and dsn:
            try:
                await barrer(cola, registrador)
            except Exception as e:                           # noqa: BLE001
                # Que el barrido falle no puede impedir que el servicio arranque:
                # su trabajo real es drenar la cola.
                log.error("el barrido de huérfanos falló: %s", e)

        await bucle(cola, registrador, BARRIDO_S, fabricador)
    finally:
        await cola.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Provisioner detenido.")


if __name__ == "__main__":
    main()
