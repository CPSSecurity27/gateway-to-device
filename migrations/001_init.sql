-- ============================================================================
-- GtD — esquema base (CONTRATO para el equipo web).
--
-- Postgres todavía no existe en el servidor: este archivo DEFINE las tablas y
-- los canales NOTIFY que el GtD espera. El equipo web lo aplica y ajusta al
-- integrar. El GtD (StubRepo) funciona sin esto; PgRepo lo requiere.
--
-- Principio: Postgres es la única fuente de verdad y el bus entre el GtD y el
-- backend de app. El GtD escribe/lee acá; el backend de app también. Nunca se
-- hablan directo.
-- ============================================================================

-- ── Estado presente de cada panel (una fila por equipo) ─────────────────────
-- Lo actualiza el GtD con status/tele. Lo lee el backend de app.
CREATE TABLE IF NOT EXISTS panel_state (
    mac          TEXT PRIMARY KEY,              -- AV-XXXXXXXXXXXX (12 hex)
    online       BOOLEAN     NOT NULL DEFAULT FALSE,
    modo_energia TEXT,                           -- status.modo / energia.modo
    alarma_mode  TEXT,                           -- slug: off|suspicious|...|panic
    cfg_v        BIGINT      NOT NULL DEFAULT 0, -- última cfg aplicada (reportada)
    rf_gen       BIGINT      NOT NULL DEFAULT 0,
    energia      JSONB,                          -- {vbat,vpanel,vfuente,...}
    last_seen    TIMESTAMPTZ,                    -- ts del último mensaje del panel
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Eventos de subida (stream av/<mac>/up) ──────────────────────────────────
-- Alarmas, acks, scans, ota. eid/cid dan idempotencia (QoS1 redistribuye).
CREATE TABLE IF NOT EXISTS eventos (
    id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mac         TEXT        NOT NULL,
    tipo        TEXT        NOT NULL,             -- alarma|ack|scan|ota
    eid         TEXT,                             -- <boot_id>-<seq> (alarmas)
    payload     JSONB       NOT NULL,             -- documento crudo completo
    ts          TIMESTAMPTZ,                      -- ts del panel
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Dedup de alarmas: un mismo eid por panel se inserta una sola vez.
CREATE UNIQUE INDEX IF NOT EXISTS ux_eventos_mac_eid
    ON eventos (mac, eid) WHERE eid IS NOT NULL;

-- ── Comandos S→D (downlink) ─────────────────────────────────────────────────
-- El backend de app INSERTA en 'pending'. El GtD publica y marca 'sent'.
-- La confirmación entra por el uplink (up t:alarma con el mismo cid) → 'confirmed'.
CREATE TABLE IF NOT EXISTS commands (
    cid          TEXT PRIMARY KEY,               -- correlación extremo a extremo
    mac          TEXT        NOT NULL,
    tipo         TEXT        NOT NULL,            -- alarma|restart|ota|...
    payload      JSONB       NOT NULL,            -- documento a publicar en cmd
    estado       TEXT        NOT NULL DEFAULT 'pending',  -- pending|sent|confirmed|failed
    detalle      TEXT,                            -- del ack (res/det)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at      TIMESTAMPTZ,
    confirmed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_commands_pending
    ON commands (mac) WHERE estado = 'pending';

-- ── Config deseada S→D (retained) ───────────────────────────────────────────
-- Estado deseado por panel. cfg_v es el árbitro de versión (idempotencia).
-- payload lleva secretos (passwords WiFi) → CIFRAR EN REPOSO (pgcrypto/app).
CREATE TABLE IF NOT EXISTS panel_config (
    mac        TEXT PRIMARY KEY,
    cfg_v      BIGINT      NOT NULL,
    payload    JSONB       NOT NULL,             -- cfg_full (cifrado)
    estado     TEXT        NOT NULL DEFAULT 'pending',  -- pending|sent|applied
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Canales NOTIFY (el bus pub/sub interno) ─────────────────────────────────
-- GtD  LISTEN 'gtd_commands', 'gtd_config'  → despacha downlink.
-- App  LISTEN 'app_panel_state'             → empuja a la app (WS/push).
CREATE OR REPLACE FUNCTION notify_gtd_commands() RETURNS trigger AS $$
BEGIN
    IF NEW.estado = 'pending' THEN
        PERFORM pg_notify('gtd_commands', NEW.mac);
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION notify_gtd_config() RETURNS trigger AS $$
BEGIN
    IF NEW.estado = 'pending' THEN
        PERFORM pg_notify('gtd_config', NEW.mac);
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION notify_app_panel_state() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('app_panel_state', NEW.mac);
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_commands_notify ON commands;
CREATE TRIGGER trg_commands_notify AFTER INSERT OR UPDATE ON commands
    FOR EACH ROW EXECUTE FUNCTION notify_gtd_commands();

DROP TRIGGER IF EXISTS trg_config_notify ON panel_config;
CREATE TRIGGER trg_config_notify AFTER INSERT OR UPDATE ON panel_config
    FOR EACH ROW EXECUTE FUNCTION notify_gtd_config();

DROP TRIGGER IF EXISTS trg_panel_state_notify ON panel_state;
CREATE TRIGGER trg_panel_state_notify AFTER INSERT OR UPDATE ON panel_state
    FOR EACH ROW EXECUTE FUNCTION notify_app_panel_state();
