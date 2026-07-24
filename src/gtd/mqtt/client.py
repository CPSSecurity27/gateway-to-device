"""Configuración del cliente aiomqtt (TLS + sesión persistente).

Thin wrapper: aiomqtt ya es delgado. Acá solo se arma el contexto TLS y los
kwargs del Client con la política del GtD (sesión persistente, keepalive).
La supervisión/reconexión vive en __main__ (bucle de servicio).
"""

from __future__ import annotations

import ssl

import aiomqtt

from ..settings import Settings


def build_tls_context(settings: Settings) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if settings.mqtt_ca_file:
        # CA propia (ej. si el broker no usa un cert de CA pública).
        ctx.load_verify_locations(cafile=settings.mqtt_ca_file)
    # Con cert Let's Encrypt + bundle del sistema, esto valida contra el hostname.
    return ctx


def client(settings: Settings) -> aiomqtt.Client:
    """Cliente configurado. Usar como `async with client(settings) as c:`.

    clean_session=False → el broker retiene suscripciones y encola QoS1 mientras
    el GtD está caído: no se pierden mensajes en una reconexión corta.
    """
    return aiomqtt.Client(
        hostname=settings.mqtt_host,
        port=settings.mqtt_port,
        username=settings.mqtt_username,
        password=settings.mqtt_password or None,
        tls_context=build_tls_context(settings),
        identifier=settings.mqtt_client_id,
        clean_session=False,
        keepalive=60,
    )
