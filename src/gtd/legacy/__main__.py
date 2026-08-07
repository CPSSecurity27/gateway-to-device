"""Puente con la app VIEJA de vecinos.

    python -m gtd.legacy

TEMPORAL: existe solo mientras haya clientes que no actualizaron la app. El día
que no quede ninguno se apaga la unidad, se dropea la migración LegacyAppBridge
y se borra este paquete. Está todo junto en un proceso justamente para que ese
día sea una tarde y no una arqueología.

Es el TERCER proceso, aparte del GtD y del provisioner, y por las mismas razones
de siempre:

- Habla con el listener **1883 en claro y anónimo**, que es el que usa la app
  vieja. El GtD habla 8883 con TLS y credenciales: son dos clientes MQTT con
  políticas opuestas, y mezclarlos sería meter una conexión sin autenticar
  adentro del proceso que recibe los payloads de cada panel.
- Se conecta a Postgres con el rol `cps_legacy`, que tiene EXECUTE sobre UNA
  función y nada más — ni siquiera puede leer `device`.
- Se apaga solo, sin tocar nada más.

**No puede correr junto con `broker-bridge.service`**: los dos consumen
`cliente/servidor` y el barrio recibiría todo dos veces. El corte es apagar uno
y prender el otro.
"""

from __future__ import annotations

import asyncio
import logging

import aiomqtt
import httpx

from ..obs import logging as obs
from ..settings import Settings
from . import aviso as aviso_mod
from . import rtdb as rtdb_mod
from .aviso import Fcm
from .espejo import Espejo
from .freno import Freno
from .google import CuentaDeServicio
from .proyector import bucle as bucle_proyector
from .puerta import Puerta, PuertaPg, PuertaStub
from .rtdb import Rtdb
from .servicio import bucle

log = logging.getLogger("gtd.legacy")

# Cuánto esperar antes de reconectar al broker. La app vieja publica con QoS 1 y
# `clean_session` propia; mientras estemos caídos los mensajes se pierden, así
# que conviene volver rápido.
RECONEXION_S = 5.0


async def run() -> None:
    settings = Settings()
    obs.setup(settings.log_level)

    # httpx loguea CADA request en INFO. Con el barrido eso son cientos de
    # líneas por hora que tapan los eventos reales en el journal — que es
    # justamente lo que uno va a mirar cuando algo ande mal. Los errores siguen
    # saliendo: los levanta y los loguea el código que llama.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    puerta: Puerta
    if settings.legacy_dsn:
        puerta = PuertaPg(settings.legacy_dsn)
    else:
        # Grita, no susurra: un puente que dice "ok" sin encolar nada es
        # exactamente lo que no querés que pase inadvertido en producción.
        log.warning(
            "=== Sin GTD_LEGACY_DSN: la app vieja NO va a poder activar nada. "
            "Se aceptan los mensajes y se tiran. NO usar en producción. ===",
        )
        puerta = PuertaStub()

    freno = Freno(
        por_dni_s=settings.legacy_freno_dni_s,
        global_por_min=settings.legacy_freno_global_por_min,
    )

    await puerta.start()
    async with httpx.AsyncClient() as http:
        tareas = [asyncio.create_task(_subida(settings, puerta, freno))]

        proy = _armar_proyeccion(settings, http)
        if proy is None:
            log.warning(
                "=== Sin GTD_LEGACY_SA_FILE: no hay proyección ni push. La app "
                "vieja va a poder ACTIVAR, pero sus pantallas se quedan con el "
                "último valor que escribió el broker-bridge — o sea mostrando "
                "una activación vieja como si fuera de ahora. ===",
            )
        else:
            rtdb, espejo, fcm = proy
            tareas.append(asyncio.create_task(
                bucle_proyector(
                    puerta, espejo, rtdb, fcm,
                    settings.legacy_barrido_s, settings.legacy_clientes_s,
                ),
            ))

        try:
            # Si cualquiera de los dos bucles muere, el proceso entero baja y
            # systemd lo levanta. Medio puente andando es peor que ninguno: el
            # vecino activaría sin ver nada, o vería sin poder activar.
            await asyncio.gather(*tareas)
        finally:
            for t in tareas:
                t.cancel()
            await puerta.close()


def _armar_proyeccion(
    settings: Settings, http: httpx.AsyncClient,
) -> tuple[Rtdb, Espejo, Fcm | None] | None:
    if not settings.legacy_sa_file:
        return None

    # Un solo canje de token para los dos scopes: la RTDB y FCM piden distintos
    # y pedirlos juntos evita mantener dos credenciales en paralelo.
    cuenta = CuentaDeServicio(
        settings.legacy_sa_file, [*rtdb_mod.SCOPES, *aviso_mod.SCOPES],
    )
    rtdb = Rtdb(
        settings.legacy_rtdb_url, cuenta, http, settings.legacy_rtdb_prefijo,
    )
    if settings.legacy_rtdb_prefijo:
        log.warning(
            "=== ENSAYO: todo se escribe bajo '%s/'. La app vieja NO lo lee. ===",
            settings.legacy_rtdb_prefijo,
        )

    fcm = Fcm(cuenta, http, cuenta.project_id) if settings.legacy_push else None
    if fcm is None:
        log.warning("Push DESACTIVADO (GTD_LEGACY_PUSH=false): nadie se entera.")

    return rtdb, Espejo(rtdb), fcm


async def _subida(settings: Settings, puerta: Puerta, freno: Freno) -> None:
    """El consumo de `cliente/servidor`, con reconexión."""
    while True:
        try:
            async with aiomqtt.Client(
                hostname=settings.legacy_mqtt_host,
                port=settings.legacy_mqtt_port,
                identifier=settings.legacy_mqtt_client_id,
                # Sin TLS y sin usuario: así es el listener viejo, y así se
                # conecta la app vieja. No es un descuido, es el contrato.
                keepalive=30,
            ) as cliente:
                log.info(
                    "[legacy] conectado a %s:%s",
                    settings.legacy_mqtt_host, settings.legacy_mqtt_port,
                )
                await bucle(cliente, settings.legacy_topic, puerta, freno)
        except aiomqtt.MqttError as e:
            log.warning(
                "[legacy] conexión perdida (%s) — reintento en %.0f s",
                e, RECONEXION_S,
            )
            await asyncio.sleep(RECONEXION_S)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Puente legacy detenido.")


if __name__ == "__main__":
    main()
