"""Capa de acceso a datos — ÚNICO lugar que habla con la base.

`Repo` es la interfaz (Protocol). `StubRepo` la implementa in-memory + logs, para
desarrollar y testear el lado MQTT SIN Postgres (que todavía no existe en el
servidor). El equipo web implementa `PgRepo(Repo)` con asyncpg contra el esquema
de migrations/001_init.sql — sin tocar los pipelines.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("gtd.repo")


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
    """TODO (equipo web): implementar contra migrations/001_init.sql con asyncpg.

    Debe cumplir el Protocol `Repo`. Puntos de atención:
      - insert_evento: ON CONFLICT (mac, eid) DO NOTHING → RETURNING para el bool.
      - upsert_config_espejo: arbitrar por cfg_v (no pisar con una versión vieja).
      - los NOTIFY los disparan los triggers del .sql, no este código.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def start(self) -> None:
        raise NotImplementedError(
            "PgRepo lo integra el equipo web cuando exista Postgres. "
            "Interín: dejar GTD_PG_DSN vacío para usar StubRepo."
        )
