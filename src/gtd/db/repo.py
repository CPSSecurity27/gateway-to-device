"""Capa de acceso a datos — ÚNICO lugar que habla con la base.

`Repo` es la interfaz (Protocol). `StubRepo` la implementa in-memory + logs
(desarrollo sin Postgres). `PgRepo` es la real: llama las FUNCIONES del esquema
`gtd` de la base del sistema web (contrato-gtd-postgres.md) — el rol
`cps_alarms` no puede tocar ninguna tabla, solo EXECUTE sobre esas funciones,
y eso lo impone el motor, no este código.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import asyncpg

log = logging.getLogger("gtd.repo")


class RepoUnavailable(RuntimeError):
    """Postgres no responde y los reintentos acotados se agotaron. El caller
    decide qué hacer (el canal `up` va al spool en disco; ver pipeline/uplink)."""


@runtime_checkable
class Repo(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...

    # ── uplink (panel → base) ──
    async def upsert_panel_state(
        self, mac: str, *, estado: str | None = None,       # 'online'|'durmiendo'|'offline'
        modo_energia: str | None = None, alarma_mode: str | None = None,
        cfg_v: int | None = None, rf_gen: int | None = None,
        energia: dict[str, Any] | None = None, fw: str | None = None,
        despierta: int | None = None,                        # unix s (estado durmiendo)
        ts: int | None = None, tsq: int | None = None,       # reloj DECLARADO del panel
        seen: bool = True,                                   # False = watchdog: el panel NO habló
    ) -> None: ...

    async def insert_evento(
        self, mac: str, tipo: str, payload: dict[str, Any],
        *, eid: str | None = None, ts: int | None = None,
    ) -> bool:
        """False si el eid ya existía (dedup idempotente)."""
        ...

    async def confirm_command(
        self, cid: str, *, res: str | None = None, det: str | None = None,
    ) -> None: ...

    async def upsert_config_espejo(
        self, mac: str, cfg_v: int, payload: dict[str, Any],
    ) -> None: ...

    # ── downlink (base → panel) ──
    async def fetch_pending_commands(self, mac: str) -> list[dict[str, Any]]: ...
    async def fetch_pending_config(self, mac: str) -> dict[str, Any] | None: ...
    async def mark_command_sent(self, cid: str) -> None: ...
    async def mark_config_sent(self, mac: str, cfg_v: int) -> None: ...
    async def mark_config_failed(self, mac: str, cfg_v: int, det: str) -> None:
        """La cfg que NO se pudo entregar (ej: no entra en los 1024 del panel)."""
        ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StubRepo:
    """Implementación de memoria. NO persiste. Sirve para el interín sin Postgres."""

    def __init__(self) -> None:
        self.panel_state: dict[str, dict[str, Any]] = {}
        self.eventos: list[dict[str, Any]] = []
        self._seen_eids: set[tuple[str, str]] = set()   # (mac, eid) → dedup

    async def start(self) -> None:
        log.warning("StubRepo activo (sin Postgres): nada se persiste.")

    async def close(self) -> None:
        pass

    async def upsert_panel_state(self, mac: str, **fields: Any) -> None:
        row = self.panel_state.setdefault(mac, {"mac": mac})
        row.update({k: v for k, v in fields.items() if v is not None})
        row["updated_at"] = _now()
        log.info("panel_state[%s] ← %s", mac, {k: v for k, v in fields.items() if v is not None})

    async def insert_evento(self, mac, tipo, payload, *, eid=None, ts=None) -> bool:
        if eid is not None:
            key = (mac, eid)
            if key in self._seen_eids:
                log.info("evento duplicado ignorado mac=%s eid=%s", mac, eid)
                return False
            self._seen_eids.add(key)
        self.eventos.append({"mac": mac, "tipo": tipo, "eid": eid,
                             "payload": payload, "ts": ts, "received_at": _now()})
        log.info("evento[%s] tipo=%s eid=%s", mac, tipo, eid)
        return True

    async def confirm_command(self, cid, *, res=None, det=None) -> None:
        log.info("command confirmado cid=%s res=%s det=%s", cid, res, det)

    async def upsert_config_espejo(self, mac, cfg_v, payload) -> None:
        log.info("config espejo[%s] cfg_v=%s", mac, cfg_v)

    async def fetch_pending_commands(self, mac) -> list[dict[str, Any]]:
        return []   # sin base no hay pendientes: el downlink queda dormido

    async def fetch_pending_config(self, mac) -> dict[str, Any] | None:
        return None

    async def mark_command_sent(self, cid) -> None:
        pass

    async def mark_config_sent(self, mac, cfg_v) -> None:
        pass

    async def mark_config_failed(self, mac, cfg_v, det) -> None:
        log.warning("cfg failed mac=%s cfg_v=%s: %s", mac, cfg_v, det)


class PgRepo:
    """Repo real contra las funciones del esquema `gtd` (SECURITY DEFINER).

    - Notación NOMBRADA en todas las llamadas: desacopla del orden de la firma
      (P2-5 del doc 06).
    - NULL = no tocar: se mandan TODOS los parámetros, None donde no hay dato.
    - Estado (upsert_panel_state / espejo / bajada): reintenta PARA SIEMPRE con
      backoff — es idempotente y bloquear el uplink es el backpressure correcto
      (la sesión persistente del broker encola mientras tanto).
    - Eventos (insert_evento / confirm_command): reintentos ACOTADOS y después
      RepoUnavailable — una alarma no puede esperar para siempre en memoria;
      para eso está el spool en disco.
    """

    RETRY_BASE_S = 1.0
    RETRY_MAX_S = 30.0
    EVENT_RETRIES = 3

    _ERRORES_CONEXION = (asyncpg.PostgresConnectionError, asyncpg.InterfaceError,
                         ConnectionError, OSError, TimeoutError)

    _SQL_UPSERT_STATE = """
        SELECT gtd.upsert_panel_state(
            p_mac => $1, p_estado => $2, p_modo_energia => $3, p_alarma_mode => $4,
            p_cfg_v => $5, p_rf_gen => $6, p_energia => $7, p_fw => $8,
            p_despierta => $9, p_ts_device => $10, p_tsq => $11, p_seen => $12)
    """
    _SQL_INSERT_EVENTO = """
        SELECT gtd.insert_evento(p_mac => $1, p_tipo => $2, p_payload => $3,
                                 p_eid => $4, p_ts => $5)
    """
    _SQL_CONFIRM = "SELECT gtd.confirm_command(p_cid => $1, p_res => $2, p_det => $3)"
    _SQL_ESPEJO = "SELECT gtd.upsert_config_espejo(p_mac => $1, p_cfg_v => $2, p_payload => $3)"
    _SQL_FETCH_CMDS = "SELECT cid, tipo, payload FROM gtd.fetch_pending_commands($1)"
    _SQL_FETCH_CFG = "SELECT cfg_v, payload FROM gtd.fetch_pending_config($1)"
    _SQL_MARK_CMD = "SELECT gtd.mark_command_sent(p_cid => $1)"
    _SQL_MARK_CFG = "SELECT gtd.mark_config_sent(p_mac => $1, p_cfg_v => $2)"
    _SQL_MARK_CFG_FAILED = (
        "SELECT gtd.mark_config_failed(p_mac => $1, p_cfg_v => $2, p_det => $3)"
    )

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @staticmethod
    async def _init_conn(conn: asyncpg.Connection) -> None:
        # SIN el códec, los dict llegan como texto — el error número uno
        # anticipado en la guía del equipo web (§2).
        await conn.set_type_codec("jsonb", encoder=json.dumps,
                                  decoder=json.loads, schema="pg_catalog")

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=1, max_size=4, init=self._init_conn)
        log.info("PgRepo conectado")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _fetchval(self, sql: str, *args, intentos: int | None = None):
        """intentos=None ⇒ reintenta para siempre. intentos=N ⇒ RepoUnavailable."""
        delay, fallos = self.RETRY_BASE_S, 0
        while True:
            try:
                async with self._pool.acquire() as conn:
                    return await conn.fetchval(sql, *args)
            except self._ERRORES_CONEXION as e:
                fallos += 1
                if intentos is not None and fallos >= intentos:
                    raise RepoUnavailable(str(e)) from e
                log.warning("Postgres no responde (%s) — reintento en %.0fs", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.RETRY_MAX_S)

    async def _fetch(self, sql: str, *args):
        delay = self.RETRY_BASE_S
        while True:
            try:
                async with self._pool.acquire() as conn:
                    return await conn.fetch(sql, *args)
            except self._ERRORES_CONEXION as e:
                log.warning("Postgres no responde (%s) — reintento en %.0fs", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.RETRY_MAX_S)

    # ── uplink (panel → base) ──
    async def upsert_panel_state(self, mac, *, estado=None, modo_energia=None,
                                 alarma_mode=None, cfg_v=None, rf_gen=None,
                                 energia=None, fw=None, despierta=None,
                                 ts=None, tsq=None, seen=True) -> None:
        res = await self._fetchval(self._SQL_UPSERT_STATE, mac, estado, modo_energia,
                                   alarma_mode, cfg_v, rf_gen, energia, fw,
                                   despierta, ts, tsq, seen)
        if res != "ok":
            # Las funciones no tiran excepción para el GtD: devuelven códigos.
            log.warning("upsert_panel_state mac=%s → %s", mac, res)

    async def insert_evento(self, mac, tipo, payload, *, eid=None, ts=None) -> bool:
        return await self._fetchval(self._SQL_INSERT_EVENTO, mac, tipo, payload,
                                    eid, ts, intentos=self.EVENT_RETRIES)

    async def confirm_command(self, cid, *, res=None, det=None) -> None:
        r = await self._fetchval(self._SQL_CONFIRM, cid, res, det,
                                 intentos=self.EVENT_RETRIES)
        if r != "ok":
            log.warning("confirm_command cid=%s → %s", cid, r)

    async def upsert_config_espejo(self, mac, cfg_v, payload) -> None:
        r = await self._fetchval(self._SQL_ESPEJO, mac, cfg_v, payload)
        if r != "ok":
            log.warning("upsert_config_espejo mac=%s → %s", mac, r)

    # ── downlink (base → panel) ──
    async def fetch_pending_commands(self, mac) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._fetch(self._SQL_FETCH_CMDS, mac)]

    async def fetch_pending_config(self, mac) -> dict[str, Any] | None:
        filas = await self._fetch(self._SQL_FETCH_CFG, mac)
        return dict(filas[0]) if filas else None

    async def mark_command_sent(self, cid) -> None:
        await self._fetchval(self._SQL_MARK_CMD, cid)

    async def mark_config_sent(self, mac, cfg_v) -> None:
        await self._fetchval(self._SQL_MARK_CFG, mac, cfg_v)

    async def mark_config_failed(self, mac, cfg_v, det) -> None:
        await self._fetchval(self._SQL_MARK_CFG_FAILED, mac, cfg_v, det)
