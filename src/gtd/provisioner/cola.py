"""Acceso a `gtd.provisioning_queue`.

El provisioner NO toca la tabla: llama las dos funciones que le dejaron, igual
que el GtD con las suyas. Su rol (`cps_provisioner`) no puede encolar —eso es de
la web— ni ejecutar ninguna función del GtD.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger("gtd.provisioner.cola")


@dataclass(frozen=True)
class Pendiente:
    id: int
    mac: str
    op: str          # "provision" | "revoke"


class Cola(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def pendientes(self) -> list[Pendiente]: ...
    async def confirmar(self, id_: int, res: str, det: str | None) -> None: ...


class ColaStub:
    """Cola de memoria, para test y para correr sin base."""

    def __init__(self, filas: list[Pendiente] | None = None) -> None:
        self._filas = list(filas or [])
        self.confirmaciones: list[tuple[int, str, str | None]] = []

    async def start(self) -> None:
        log.warning("ColaStub activa (sin Postgres): nada que drenar.")

    async def close(self) -> None:
        pass

    async def pendientes(self) -> list[Pendiente]:
        return list(self._filas)

    async def confirmar(self, id_: int, res: str, det: str | None) -> None:
        self._filas = [f for f in self._filas if f.id != id_]
        self.confirmaciones.append((id_, res, det))


class ColaPg:
    """Contra Postgres, con asyncpg. Conexión local: la base está en la misma
    máquina y escucha solo en 127.0.0.1."""

    _SQL_FETCH = "SELECT id, mac, op FROM gtd.fetch_pending_provisioning()"
    _SQL_CONFIRM = (
        "SELECT gtd.confirm_provisioning(p_id => $1, p_res => $2, p_det => $3)"
    )

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None

    async def start(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=2)
        log.info("ColaPg conectada")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def pendientes(self) -> list[Pendiente]:
        async with self._pool.acquire() as conn:
            filas = await conn.fetch(self._SQL_FETCH)
        return [Pendiente(f["id"], f["mac"], f["op"]) for f in filas]

    async def confirmar(self, id_: int, res: str, det: str | None) -> None:
        async with self._pool.acquire() as conn:
            r = await conn.fetchval(self._SQL_CONFIRM, id_, res, det)
        # 'noop' es normal: la fila ya estaba cerrada (dos barridos pisándose).
        if r not in ("ok", "noop"):
            log.warning("confirm_provisioning id=%s → %s", id_, r)
