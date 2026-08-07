"""Todo el acceso a Postgres del puente, en las dos direcciones.

**Subida** (`enqueue_legacy_alarm`): el adaptador es TONTO a propósito. No elige
a qué equipo le pega, no valida cupos, no lee tablas y no escribe SQL: traduce
un mensaje MQTT en una llamada a función y loguea lo que le contestaron. Toda la
decisión —y el motivo del rechazo— vive adentro de la base, en un solo lugar.

**Bajada** (`legacy_snapshot` / `legacy_devices` / `legacy_clientes`): lo mismo
al revés. El proyector no arma consultas: pide un snapshot ya resuelto.

El rol `cps_legacy` tiene EXECUTE sobre esas cuatro funciones y NADA más — ni
siquiera SELECT sobre `device`. Si un día este proceso queda expuesto (su
entrada es un tópico MQTT anónimo abierto a internet), lo peor que puede hacer
es pedir activaciones que la función ya sabe rechazar.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .espejo import Snapshot
from .mensaje import Activacion

log = logging.getLogger("gtd.legacy.puerta")

# La base avisa acá que un evento se abrió o se cerró. El payload es el
# device_id. No es un canal del legado: le va a servir igual al backend de la
# app nueva, por eso se llama `app_event` y sobrevive al apagado del puente.
CANAL_EVENTO = "app_event"

# Lo que devuelve la función cuando salió bien. Cualquier otra cosa es el motivo
# del rechazo y se loguea tal cual: los nombres son del SQL, no de acá, para que
# buscar en el journal y buscar en la migración den lo mismo.
OK = "ok"


@dataclass(frozen=True)
class Resultado:
    cid: str | None
    resultado: str

    @property
    def acepto(self) -> bool:
        return self.resultado == OK


class Puerta(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def activar(self, a: Activacion) -> Resultado: ...
    async def snapshot(self, device_id: int) -> Snapshot | None: ...
    async def devices(self) -> list[tuple[int, str]]: ...
    async def clientes(self) -> list[dict[str, Any]]: ...
    async def proximo_evento(self, timeout: float) -> int | None: ...


class PuertaStub:
    """Sin base: acepta todo y no hace nada. Para test y desarrollo."""

    def __init__(self, snapshots: dict[int, Snapshot] | None = None) -> None:
        self.pedidos: list[Activacion] = []
        self._snapshots = snapshots or {}

    async def start(self) -> None:
        log.warning("PuertaStub activa (sin Postgres): no se encola nada de verdad.")

    async def close(self) -> None:
        pass

    async def activar(self, a: Activacion) -> Resultado:
        self.pedidos.append(a)
        return Resultado(cid=f"stub-{len(self.pedidos)}", resultado=OK)

    async def snapshot(self, device_id: int) -> Snapshot | None:
        return self._snapshots.get(device_id)

    async def devices(self) -> list[tuple[int, str]]:
        return [(d, s.marcador) for d, s in self._snapshots.items()]

    async def clientes(self) -> list[dict[str, Any]]:
        return []

    async def proximo_evento(self, timeout: float) -> int | None:
        await asyncio.sleep(timeout)
        return None


class PuertaPg:
    """Contra Postgres, con asyncpg. La base está en la misma máquina y escucha
    solo en 127.0.0.1."""

    _SQL = (
        "SELECT cid, resultado FROM gtd.enqueue_legacy_alarm("
        "p_dni => $1, p_code => $2, p_lat => $3, p_lng => $4)"
    )
    _SQL_SNAPSHOT = "SELECT * FROM gtd.legacy_snapshot($1)"
    _SQL_DEVICES = "SELECT device_id, marcador FROM gtd.legacy_devices()"
    _SQL_CLIENTES = "SELECT * FROM gtd.legacy_clientes()"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None
        self._escucha: Any = None
        self._eventos: asyncio.Queue[int] = asyncio.Queue()

    async def start(self) -> None:
        import asyncpg

        # Pool chico: el volumen es de decenas de mensajes por día, no por
        # segundo. Lo que importa es que una activación no espere a otra.
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=3)

        # Conexión DEDICADA para el LISTEN: una del pool se devolvería y el
        # listener moriría con ella. Mismo criterio que ColaPg.
        try:
            self._escucha = await asyncpg.connect(self._dsn)
            await self._escucha.add_listener(CANAL_EVENTO, self._al_avisar)
            log.info("PuertaPg conectada — escuchando %s", CANAL_EVENTO)
        except Exception as e:                                # noqa: BLE001
            # No es fatal: el barrido periódico sigue proyectando. Es más lento
            # —el vecino ve el cambio en el próximo barrido en vez de al
            # instante— pero nada se pierde.
            self._escucha = None
            log.warning(
                "No se pudo escuchar %s (%s): la proyección va a depender solo "
                "del barrido, así que la app puede tardar en enterarse.",
                CANAL_EVENTO, e,
            )

    def _al_avisar(self, _c: Any, _pid: int, _canal: str, payload: str) -> None:
        # asyncpg llama esto desde el loop: nada de I/O acá.
        try:
            self._eventos.put_nowait(int(payload))
        except (ValueError, asyncio.QueueFull):
            log.warning("NOTIFY %s con payload raro: %r", CANAL_EVENTO, payload)

    async def proximo_evento(self, timeout: float) -> int | None:
        """El device_id del próximo evento, o None si venció el timeout."""
        try:
            return await asyncio.wait_for(self._eventos.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        if self._escucha is not None:
            await self._escucha.close()
            self._escucha = None
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def snapshot(self, device_id: int) -> Snapshot | None:
        f = await self._pool.fetchrow(self._SQL_SNAPSHOT, device_id)
        if f is None:
            return None      # el equipo no tiene legacy_marker: no se proyecta
        return Snapshot(
            marcador=f["marcador"], estado=f["estado"], event_id=f["event_id"],
            usuario=f["usuario"], telefono=f["telefono"],
            direccion=f["direccion"], modoalarma=f["modoalarma"],
            gps_lat=f["gps_lat"], gps_lng=f["gps_lng"], creado=f["creado"],
        )

    async def devices(self) -> list[tuple[int, str]]:
        return [(f["device_id"], f["marcador"])
                for f in await self._pool.fetch(self._SQL_DEVICES)]

    async def clientes(self) -> list[dict[str, Any]]:
        return [dict(f) for f in await self._pool.fetch(self._SQL_CLIENTES)]

    async def activar(self, a: Activacion) -> Resultado:
        fila = await self._pool.fetchrow(self._SQL, a.dni, a.modo, a.lat, a.lng)
        if fila is None:
            # La función siempre devuelve una fila; si no, algo cambió del otro
            # lado y es mejor decirlo que inventar un 'ok'.
            return Resultado(cid=None, resultado="sin_respuesta")
        return Resultado(cid=fila["cid"], resultado=fila["resultado"])
