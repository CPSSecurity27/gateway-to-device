"""Bucle de servicio del GtD: conecta MQTT, corre uplink + downlink, reconecta.

    python -m gtd

Dos tareas concurrentes bajo una conexión MQTT:
  - uplink:   consume av/+/{status,tele,up} → pipeline.uplink → repo
  - downlink: consume NOTIFY de Postgres    → pipeline.downlink → publica cmd/cfg

Si la conexión MQTT se cae, todo el bloque revienta con MqttError, se cancela la
otra tarea y se reintenta tras RECONNECT_S. La sesión persistente del broker
(clean_session=False) preserva suscripciones y cola durante el corte.
"""

from __future__ import annotations

import asyncio
import logging

import aiomqtt

from . import mqtt as mqtt_pkg
from .db.listener import StubListener
from .db.repo import Repo, StubRepo
from .mqtt import topics
from .obs import logging as obs
from .pipeline import downlink, presencia, uplink
from .settings import Settings

RECONNECT_S = 30   # coincide con MQTT_RECONNECT_MS del firmware
WATCHDOG_S = 30    # cada cuánto se revisa el silencio de la flota

log = logging.getLogger("gtd")


def make_repo(settings: Settings) -> Repo:
    if settings.use_postgres:
        from .db.repo import PgRepo
        return PgRepo(settings.pg_dsn)   # el equipo web lo implementa
    return StubRepo()


def make_listener(settings: Settings):
    if settings.use_postgres:
        from .db.listener import PgListener
        return PgListener(settings.pg_dsn)
    return StubListener()


async def _uplink_loop(client: aiomqtt.Client, repo: Repo, settings: Settings) -> None:
    async for message in client.messages:
        payload = message.payload
        if isinstance(payload, (bytes, bytearray)):
            raw = bytes(payload)
        else:
            raw = str(payload).encode()
        await uplink.handle(str(message.topic), raw, repo,
                            gap_reconexion=settings.reconnect_gap_s)


async def _watchdog_presencia(repo: Repo, settings: Settings) -> None:
    """Marca offline a los paneles que dejaron de hablar.

    El LWT no alcanza: en un takeover de sesión no se publica, y un panel que
    enmudece sin que venza el keepalive del broker queda `online` para siempre.

    Corre DENTRO del TaskGroup de la conexión MQTT a propósito: si el GtD pierde
    el broker, todos los paneles parecerían callados y esto los marcaría offline
    en masa. Al morir con la conexión, eso no puede pasar.
    """
    while True:
        await asyncio.sleep(WATCHDOG_S)
        for mac, callado in presencia.sin_senal(settings.presence_timeout_s):
            log.warning("[!] panel SIN SEÑAL mac=%s (%.0fs sin hablar)", mac, callado)
            # seen=False: el panel NO habló; last_seen no se toca (P1-3).
            await repo.upsert_panel_state(mac, estado="offline", seen=False)


async def _downlink_loop(client: aiomqtt.Client, listener, repo: Repo) -> None:
    while True:
        channel, mac = await listener.get()
        await downlink.handle(channel, mac, repo, client)


async def run() -> None:
    settings = Settings()
    obs.setup(settings.log_level)
    log.info("GtD arrancando — broker %s:%s, repo=%s",
             settings.mqtt_host, settings.mqtt_port,
             "Postgres" if settings.use_postgres else "Stub")

    repo = make_repo(settings)
    listener = make_listener(settings)
    await repo.start()
    await listener.start()

    try:
        while True:
            try:
                async with mqtt_pkg.client(settings) as client:
                    # Al resuscribirse llega de golpe el `status` retenido de toda
                    # la flota. Sin este reset, cada uno parecería una reconexión
                    # del panel — cuando el que reconectó fue el GtD.
                    presencia.reiniciar()
                    for sub in topics.subscriptions():
                        await client.subscribe(sub, qos=1)
                    log.info("suscripto a %s", topics.subscriptions())
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(_uplink_loop(client, repo, settings))
                        tg.create_task(_downlink_loop(client, listener, repo))
                        tg.create_task(_watchdog_presencia(repo, settings))
            except* aiomqtt.MqttError as eg:
                log.warning("MQTT caído (%s) — reintento en %ss",
                            eg.exceptions[0], RECONNECT_S)
                await asyncio.sleep(RECONNECT_S)
    finally:
        await listener.close()
        await repo.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("GtD detenido.")


if __name__ == "__main__":
    main()
