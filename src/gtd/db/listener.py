"""Escucha de NOTIFY de Postgres — dispara el downlink.

`StubListener` nunca emite (sin base no hay comandos que bajar): el GtD arranca
y el downlink queda dormido, pero el cableado está listo. `PgListener` es el
real: LISTEN sobre una conexión DEDICADA (el LISTEN necesita una conexión
abierta y estable, no una del pool transitorio — y si algún día hay pgbouncer,
este DSN tiene que ser directo al Postgres).
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

log = logging.getLogger("gtd.listener")

# Canales del contrato (contrato-gtd-postgres.md §12)
CH_COMMANDS = "gtd_commands"
CH_CONFIG = "gtd_config"

PING_S = 30          # una conexión asyncpg muerta NO avisa: hay que pincharla
RETRY_MAX_S = 30.0

_SQL_PENDIENTES = "SELECT mac, canal FROM gtd.fetch_pending_macs()"


class StubListener:
    """Cola vacía: get() espera para siempre. Sin base no hay downlink."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    async def start(self) -> None:
        log.warning("StubListener activo (sin Postgres): downlink dormido.")

    async def close(self) -> None:
        pass

    async def sweep(self) -> None:
        pass

    async def get(self) -> tuple[str, str]:
        """Devuelve (canal, mac). Bloquea hasta que haya un NOTIFY."""
        return await self._q.get()


class PgListener:
    """LISTEN gtd_commands/gtd_config sobre una conexión dedicada.

    - Ping periódico: una conexión muerta no tira error hasta que se la usa.
    - Al conectar (y reconectar) hace el BARRIDO con gtd.fetch_pending_macs():
      un NOTIFY emitido mientras no escuchábamos no vuelve nunca (P0-1).
    - NOTIFY repetidos por (canal, mac) se COLAPSAN: el fetch es por MAC, así
      que cinco notificaciones seguidas del mismo panel son un solo trabajo.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._q: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._pendientes: set[tuple[str, str]] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="pg-listener")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def get(self) -> tuple[str, str]:
        canal, mac = await self._q.get()
        self._pendientes.discard((canal, mac))
        return canal, mac

    # ── interno ──────────────────────────────────────────────
    def _encolar(self, canal: str, mac: str) -> None:
        clave = (canal, mac)
        if clave in self._pendientes:
            return
        self._pendientes.add(clave)
        self._q.put_nowait(clave)

    def _on_notify(self, _conn, _pid, canal: str, payload: str) -> None:
        self._encolar(canal, payload)   # el payload del NOTIFY es la MAC

    async def sweep(self) -> None:
        """Barrido puntual (conexión corta). Lo llama __main__ al reconectar a
        MQTT: un publish que falló a mitad de camino dejó la fila pending y el
        NOTIFY ya se consumió (P0-1, tercer caso)."""
        try:
            conn = await asyncpg.connect(self._dsn)
        except (asyncpg.PostgresError, OSError) as e:
            log.warning("sweep: sin conexión a Postgres (%s)", e)
            return
        try:
            for fila in await conn.fetch(_SQL_PENDIENTES):
                self._encolar(fila["canal"], fila["mac"])
        finally:
            await conn.close()

    async def _run(self) -> None:
        delay = 1.0
        while True:
            try:
                conn = await asyncpg.connect(self._dsn)
                try:
                    await conn.add_listener(CH_COMMANDS, self._on_notify)
                    await conn.add_listener(CH_CONFIG, self._on_notify)
                    # Barrido inicial: lo que se encoló mientras no escuchábamos.
                    for fila in await conn.fetch(_SQL_PENDIENTES):
                        self._encolar(fila["canal"], fila["mac"])
                    log.info("PgListener escuchando (%s, %s)", CH_COMMANDS, CH_CONFIG)
                    delay = 1.0
                    while True:
                        await asyncio.sleep(PING_S)
                        await conn.execute("SELECT 1")
                finally:
                    await conn.close()
            except asyncio.CancelledError:
                raise
            except (asyncpg.PostgresError, OSError) as e:
                log.warning("PgListener caído (%s) — reintento en %.0fs", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_S)
