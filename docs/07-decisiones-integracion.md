# 07 — Decisiones de la integración con la web (2026-08-04)

> **Cierra** el ping-pong de los docs 05 y 06. El 2026-08-04 se decidió liderar
> el enlace desde un solo lugar (los dos repos en la misma máquina, una sola
> cabeza): las 8 preguntas del doc 06 se resolvieron y se implementaron **en
> los dos repos a la vez**, en la rama `feat/pgrepo-enlace-web` de este repo y
> `feat/puente-gtd-postgres` del web. Nada estaba desplegado, así que no hubo
> ventanas de convivencia: las migraciones del web se editaron en el lugar y
> `PgRepo` se escribió contra la firma final.

**El contrato vive en el repo web** (`CPSSecurity27/system-web`,
`docs/contrato-gtd-postgres.md`): este repo lo CONSUME, no lo define. La guía
operativa es `docs/gtd-guia-implementacion.md` de allá.

---

## 1. Las 8 respuestas, resueltas

| # | Pedido | Resolución |
|---|---|---|
| P0-1 | `fetch_pending_macs()` | **SÍ.** `gtd.fetch_pending_macs() → setof (mac, canal)`, mismos predicados exactos que los `fetch_pending_*`. `PgListener` la corre al conectar, al reconectar a Postgres, y `__main__` la dispara tras cada reconexión MQTT (el caso del publish que falló a mitad de camino) |
| P0-2 | reportar una cfg no entregable | **SÍ.** `gtd.mark_config_failed(mac, cfg_v, det)` + estado `failed` + columna `detalle`. El trigger de NOTIFY solo dispara con pending/stale: `failed` corta el loop. Republicar desde la web vuelve a `pending` y limpia el detalle. El downlink la llama en la guarda de 1024 |
| P1-3 | `last_seen` miente con tsq malo | **SÍ, tenían razón.** `last_seen = now()` del servidor, adentro de la función; `p_last_seen` no existe más. El reloj declarado viaja aparte (`p_ts_device` + `p_tsq` → columnas en `device_state`, para auditar deriva). El watchdog manda `p_seen => false`: marca offline SIN tocar `last_seen` |
| P1-4 | `durmiendo` ≠ `offline` | **SÍ.** `p_estado` ('online'/'durmiendo'/'offline') reemplaza a `p_online`; `p_despierta` → `device_state.sleep_until`. `online` se deriva; cualquier estado explícito ≠ durmiendo limpia el sleep. La ficha del equipo en la web ya muestra "Durmiendo hasta las HH:mm" |
| P2-5 | `fw` + notación nombrada | `fw` entró en la firma. **La firma vieja NO convive**: en Postgres un parámetro nuevo con DEFAULT crea una SOBRECARGA y las llamadas viejas dan `function is not unique` — peor que cortar de una. Se cambió de un golpe con nada desplegado. Notación nombrada en todas las llamadas de `PgRepo` |
| P2-6 | ¿pgbouncer? | **Directo, sin pooler.** Queda escrito (guía §2 y `.env.example`): si algún día aparece uno, el listener lleva un DSN directo aparte |
| P2-7 | ¿cfg_full por insert_evento? | **SÍ**, para el histórico en `uplink_raw` — pero con `redes[].psw` **redactado** antes de mandar (`obs.logging.redact`): el claro ya vive en el espejo, no se duplica en una tabla append-only |
| P2-8 | DSN de desarrollo | Resuelto por geografía: `postgresql://cps_alarms:...@localhost:5432/cps_security_v2`. La integración completa está en `tests/test_pg_integracion.py` (16 casos, gated por `GTD_TEST_PG_DSN`) y **los 16 pasan** contra la base real con el rol real |

## 2. El plan §3 del doc 06, implementado entero

| | Qué | Dónde |
|---|---|---|
| a | Normalización de MAC (el bug §1) | `contract.mac_from_device_id`/`device_id_from_mac`, `topics.parse` valida 12 hex y devuelve pelada; `cmd_topic`/`cfg_topic` reponen el prefijo. `tests/test_topics_mac.py` |
| b | La base caída no mata al GtD | Reintentos con backoff en `PgRepo` (estado: para siempre; eventos: acotados + `RepoUnavailable`), **spool en disco** JSONL del canal `up` con drainer, y cinturón `except*` en `__main__` |
| c | Usar el bool de `insert_evento` | `uplink._handle_up`: una alarma duplicada por QoS 1 ya no reconfirma el comando |
| d | Guarda de 1024 en el downlink | `downlink.handle`: mide el JSON compacto, si no entra llama `mark_config_failed` con el porqué y NO publica |
| e | `PgListener` real | Conexión dedicada, ping cada 30 s, reconexión con backoff, barrido con `fetch_pending_macs` y colapso de NOTIFY por (canal, mac) |
| f | `fw` + los 4 tipos | `fw` en status → firma; `rf_rx`, `rf_rx_end`, `audit`, `audit_detalle` con modelos propios (formas verificadas contra `task_mqtt.c`) van por `insert_evento` |

## 3. Lo que sigue abierto

- **`SALT_MQTT` de producción (PA4)** — sigue siendo lo único que bloquea el
  alta masiva por derivación. Interín: `PANEL_PASSWORD` explícita.
- **Cifrado en reposo (DT2)** — y con la observación del doc 06 §4 registrada
  en el contrato: la `cfg` retenida deja las passwords en el disco del broker;
  cifrar Postgres solo mueve el eslabón débil. Propuesta al firmware en
  [08-propuestas-firmware.md](08-propuestas-firmware.md).
- **`MQTT_IN_PAYLOAD_MAX` con placa real** — la guarda ya está; falta el
  ensayo físico de una cfg de 5 redes.
- **Umbrales de batería provisorios** (12,0/11,8 V) — validar contra la
  especificación real antes de colgarles una alerta operativa.
