# 03 — Modelo de datos (Postgres)

Postgres es la fuente de verdad y el bus entre el GtD y el backend de app.
Esquema completo en [`migrations/001_init.sql`](../migrations/001_init.sql). Este
doc explica el porqué y qué debe implementar el equipo web.

> **Postgres todavía no existe en el servidor.** El GtD corre sin base con
> `StubRepo` (in-memory + logs) mientras `GTD_PG_DSN` esté vacío. El equipo web
> aplica el esquema e implementa `PgRepo`/`PgListener`.

## Guía rápida (¿qué tabla toca cada dato?)

**Subida** (lo que llega por MQTT → escribe la base):

| Llega... | Toca | Operación |
|---|---|---|
| `status` (online/durmiendo/offline) | `panel_state` | upsert (online, last_seen) |
| `tele` | `panel_state` | upsert (modo, alarma_mode, cfg_v, rf_gen, energia) |
| `up t:alarma` | `eventos` (+ `commands` si trae `cid`) | insert (dedup por `eid`) + confirma comando |
| `up t:ack` | `commands` + `eventos` | confirma (`cid`/`cfg_v`) + insert |
| `up t:scan` / `t:ota` | `eventos` | insert |

**Bajada** (lo que otro escribe en la base → el GtD publica por MQTT):

| Escribe (backend/web) | Dispara | El GtD hace |
|---|---|---|
| `commands` en `pending` | `NOTIFY gtd_commands` | publica `av/<mac>/cmd` |
| `panel_config` en `pending` | `NOTIFY gtd_config` | publica `av/<mac>/cfg` (retained) |

**El backend de app** escucha `NOTIFY app_panel_state` (lo dispara cualquier
cambio de `panel_state`) para empujar a la app.

## Tablas

| Tabla | Escribe | Lee | Rol |
|---|---|---|---|
| `panel_state` | GtD (status/tele) | backend app | estado presente de cada panel (1 fila/equipo) |
| `eventos` | GtD (up) | backend app | historial: alarmas, acks, scans, ota |
| `commands` | backend app (`pending`) → GtD (`sent`/`confirmed`) | ambos | comandos S→D con ciclo por `cid` |
| `panel_config` | backend app / web (`pending`) → GtD (`sent`) | GtD | config deseada (cfg_v árbitro) |

- `eventos` tiene `UNIQUE (mac, eid)` parcial → **dedup idempotente** de alarmas.
- `commands.cid` es PK → correlación extremo a extremo. Ciclo:
  `pending → sent → confirmed | failed`.
- `panel_config.payload` lleva **secretos (passwords WiFi)** → **cifrar en reposo**
  (pgcrypto o cifrado a nivel app). Nunca en logs.

## Canales NOTIFY (el bus pub/sub)

| Canal | Lo dispara | Lo escucha | Para |
|---|---|---|---|
| `gtd_commands` | INSERT/UPDATE en `commands` (pending) | **GtD** | publicar `cmd` |
| `gtd_config` | INSERT/UPDATE en `panel_config` (pending) | **GtD** | publicar `cfg` |
| `app_panel_state` | INSERT/UPDATE en `panel_state` | **backend app** | empujar a la app |

El payload del `NOTIFY` es la `mac`. Los triggers están en el `.sql`.

## Qué debe implementar el equipo web

### `PgRepo(Repo)` — `src/gtd/db/repo.py`
Cumplir el Protocol `Repo` con asyncpg. Puntos de atención:
- `insert_evento`: `INSERT ... ON CONFLICT (mac, eid) DO NOTHING RETURNING` → el
  bool sale de si insertó (dedup).
- `upsert_config_espejo`: arbitrar por `cfg_v` — no pisar con una versión más vieja
  que la ya guardada.
- Los `NOTIFY` los disparan los triggers, no el código.

### `PgListener` — `src/gtd/db/listener.py`
`LISTEN gtd_commands, gtd_config` sobre una conexión **dedicada** (no del pool):
```python
conn = await asyncpg.connect(dsn)
await conn.add_listener("gtd_commands", lambda *a: q.put_nowait(("gtd_commands", a[3])))
await conn.add_listener("gtd_config",   lambda *a: q.put_nowait(("gtd_config",   a[3])))
# get() devuelve (canal, mac) desde la cola
```

Al implementar ambos, **los pipelines (`uplink.py`/`downlink.py`) no se tocan**:
el GtD selecciona `PgRepo`/`PgListener` automáticamente cuando `GTD_PG_DSN` está
seteado (ver `__main__.py::make_repo`/`make_listener`).

## Flujo de un comando (extremo a extremo)

```
1. backend app: ¿user dueño del panel? (SQL) → INSERT commands(cid, mac, tipo, payload, pending)
2. trigger → NOTIFY gtd_commands (mac)
3. GtD: fetch_pending_commands(mac) → publish av/<mac>/cmd {..., cid} → mark_command_sent
4. panel ejecuta → up t:alarma / t:ack {cid}
5. GtD: confirm_command(cid) → UPDATE commands SET confirmed + UPDATE panel_state
6. trigger → NOTIFY app_panel_state → backend app empuja "hecho ✓" a la app
```
