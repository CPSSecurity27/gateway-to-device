"""Escucha de NOTIFY de Postgres — dispara el downlink.

`StubListener` nunca emite (sin base no hay comandos que bajar): el GtD arranca
y el downlink queda dormido, pero el cableado está listo. `PgListener` lo integra
el equipo web con `asyncpg.Connection.add_listener` sobre una conexión DEDICADA
(el LISTEN necesita una conexión abierta y estable, no una del pool transitorio).
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("gtd.listener")

# Canales del contrato (migrations/001_init.sql)
CH_COMMANDS = "gtd_commands"
CH_CONFIG = "gtd_config"


class StubListener:
    """Cola vacía: get() espera para siempre. Sin base no hay downlink."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    async def start(self) -> None:
        log.warning("StubListener activo (sin Postgres): downlink dormido.")

    async def close(self) -> None:
        pass

    async def get(self) -> tuple[str, str]:
        """Devuelve (canal, mac). Bloquea hasta que haya un NOTIFY."""
        return await self._q.get()


class PgListener:
    """TODO (equipo web): LISTEN 'gtd_commands','gtd_config' sobre asyncpg.

    Bosquejo:
        conn = await asyncpg.connect(dsn)
        await conn.add_listener(CH_COMMANDS, lambda *a: q.put_nowait((CH_COMMANDS, a[3])))
        await conn.add_listener(CH_CONFIG,   lambda *a: q.put_nowait((CH_CONFIG,   a[3])))
    El payload del NOTIFY es la mac (ver los triggers pg_notify del .sql).
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def start(self) -> None:
        raise NotImplementedError("PgListener lo integra el equipo web.")
