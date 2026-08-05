"""Acceso a `gtd.provisioning_queue`.

El provisioner NO toca la tabla: llama las dos funciones que le dejaron, igual
que el GtD con las suyas. Su rol (`cps_provisioner`) no puede encolar —eso es de
la web— ni ejecutar ninguna función del GtD.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

# El canal por el que la base avisa que hay trabajo. Lo dispara un trigger sobre
# la cola; el payload es la MAC, que acá no se usa: alcanza con saber que hay
# algo pendiente y volver a mirar la tabla.
CANAL = "gtd_provisioning"

log = logging.getLogger("gtd.provisioner.cola")


@dataclass(frozen=True)
class Pendiente:
    id: int
    mac: str
    op: str          # "provision" | "revoke" | "manufacture"

    @property
    def es_fabricacion(self) -> bool:
        """`manufacture` = registrar en el broker Y derivar las del portal.

        Es una op y no dos porque el alta de la web es ATÓMICA: espera una sola
        confirmación y, si no llega, borra el equipo. Dos ops serían dos esperas
        y dos formas de quedar a medias.
        """
        return self.op == "manufacture"


class Cola(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def pendientes(self) -> list[Pendiente]: ...
    async def esperar_trabajo(self, timeout: float) -> bool: ...
    async def confirmar(self, id_: int, res: str, det: str | None) -> None: ...
    async def confirmar_manufactura(
        self, id_: int, res: str,
        admin_enc: str | None, cps_enc: str | None, det: str | None,
    ) -> None: ...
    async def seriales_vivos(self) -> set[str]: ...


class ColaStub:
    """Cola de memoria, para test y para correr sin base."""

    def __init__(self, filas: list[Pendiente] | None = None) -> None:
        self._filas = list(filas or [])
        self.confirmaciones: list[tuple[int, str, str | None]] = []
        self.manufacturas: list[tuple[int, str, str | None, str | None, str | None]] = []
        self.seriales: set[str] = set()

    async def start(self) -> None:
        log.warning("ColaStub activa (sin Postgres): nada que drenar.")

    async def close(self) -> None:
        pass

    async def pendientes(self) -> list[Pendiente]:
        return list(self._filas)

    async def esperar_trabajo(self, timeout: float) -> bool:
        # Sin base no hay NOTIFY que esperar: se comporta como el barrido solo.
        await asyncio.sleep(timeout)
        return False

    async def confirmar(self, id_: int, res: str, det: str | None) -> None:
        self._filas = [f for f in self._filas if f.id != id_]
        self.confirmaciones.append((id_, res, det))

    async def confirmar_manufactura(
        self, id_: int, res: str,
        admin_enc: str | None, cps_enc: str | None, det: str | None,
    ) -> None:
        self._filas = [f for f in self._filas if f.id != id_]
        self.manufacturas.append((id_, res, admin_enc, cps_enc, det))

    async def seriales_vivos(self) -> set[str]:
        return set(self.seriales)


class ColaPg:
    """Contra Postgres, con asyncpg. Conexión local: la base está en la misma
    máquina y escucha solo en 127.0.0.1."""

    _SQL_FETCH = "SELECT id, mac, op FROM gtd.fetch_pending_provisioning()"
    _SQL_CONFIRM = (
        "SELECT gtd.confirm_provisioning(p_id => $1, p_res => $2, p_det => $3)"
    )
    _SQL_CONFIRM_MANUF = (
        "SELECT gtd.confirm_manufacture("
        "p_id => $1, p_res => $2, p_admin_enc => $3, p_cps_enc => $4, p_det => $5)"
    )
    # Para el barrido de huérfanos. El rol tiene SELECT solo sobre estas
    # columnas de `device`; no puede leer el resto de la tabla.
    _SQL_SERIALES = "SELECT serial FROM device WHERE mac IS NOT NULL"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None
        self._escucha: Any = None
        self._aviso = asyncio.Event()

    async def start(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=2)

        # Conexión DEDICADA para el LISTEN: una del pool se devolvería y el
        # listener moriría con ella.
        try:
            self._escucha = await asyncpg.connect(self._dsn)
            await self._escucha.add_listener(CANAL, self._al_avisar)
            log.info("ColaPg conectada — escuchando %s", CANAL)
        except Exception as e:  # noqa: BLE001
            # No es fatal: sin NOTIFY el barrido periódico sigue tomando el
            # trabajo. Es más lento, pero nada se pierde.
            self._escucha = None
            log.warning(
                "No se pudo escuchar %s (%s): se trabaja solo con el barrido, "
                "así que las altas de fábrica pueden tardar hasta un barrido "
                "entero y la web puede darlas por vencidas.",
                CANAL, e,
            )

    def _al_avisar(self, _conn: Any, _pid: int, _canal: str, _payload: str) -> None:
        # asyncpg llama esto desde el loop: solo prender la bandera, nada de I/O.
        self._aviso.set()

    async def esperar_trabajo(self, timeout: float) -> bool:
        """Espera un aviso o hasta `timeout`. True si vino por NOTIFY.

        El NOTIFY ACELERA y el barrido GARANTIZA: un aviso emitido mientras esto
        estaba caído no vuelve nunca, así que el timeout no es un lujo. Pero sin
        el aviso el alta de fábrica —que es sincrónica y la web espera 30 s— se
        vencería esperando el próximo barrido.
        """
        self._aviso.clear()
        try:
            await asyncio.wait_for(self._aviso.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        if self._escucha is not None:
            try:
                await self._escucha.remove_listener(CANAL, self._al_avisar)
                await self._escucha.close()
            except Exception:  # noqa: BLE001
                pass
            self._escucha = None
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

    async def confirmar_manufactura(
        self, id_: int, res: str,
        admin_enc: str | None, cps_enc: str | None, det: str | None,
    ) -> None:
        async with self._pool.acquire() as conn:
            r = await conn.fetchval(
                self._SQL_CONFIRM_MANUF, id_, res, admin_enc, cps_enc, det,
            )
        if r not in ("ok", "noop"):
            log.warning("confirm_manufacture id=%s → %s", id_, r)

    async def seriales_vivos(self) -> set[str]:
        async with self._pool.acquire() as conn:
            filas = await conn.fetch(self._SQL_SERIALES)
        return {f["serial"] for f in filas}
