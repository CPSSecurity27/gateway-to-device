"""Integración real: PgRepo + funciones gtd contra cps_security_v2, con el rol
cps_alarms de verdad. Los casos de la guía web §6 más los del contrato v2
(durmiendo, seen=false, failed, barrido) y los dos negativos de permisos.

Correr (PowerShell):
  $env:GTD_TEST_PG_DSN="postgresql://cps_alarms:...@localhost:5432/cps_security_v2"
  $env:GTD_TEST_PG_ADMIN_DSN="postgresql://postgres:...@localhost:5432/cps_security_v2"
  .venv\\Scripts\\python -m pytest tests/test_pg_integracion.py -v

Sin esas variables, el archivo entero se saltea (CI y desarrollo sin base).
La base es de PRUEBA: el fixture siembra sus equipos y los borra al salir.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from gtd.db.repo import PgRepo

DSN = os.environ.get("GTD_TEST_PG_DSN")
DSN_ADMIN = os.environ.get("GTD_TEST_PG_ADMIN_DSN")

pytestmark = pytest.mark.skipif(
    not (DSN and DSN_ADMIN),
    reason="sin GTD_TEST_PG_DSN/GTD_TEST_PG_ADMIN_DSN (integración local)")

MAC = "A842E38FCA6C"          # equipo OPERATIONAL con barrio
MAC_HUERFANA = "A842E38FCA70" # equipo en INVENTORY, sin barrio
MAC_FANTASMA = "FFFFFFFFFFFF" # no existe en device

ESPEJO_MINIMO = {
    "cfg_v": 1,
    "modulos": {"ds3231": True, "eeprom": True, "supervisor": True, "rf": True},
    "redes": [{"ssid": "Base", "psw": "secreta", "prio": 1}],
    "tiempos": {"send_tele_s": 300},
    "id": {"dev": "AV-" + MAC, "fw": "6.0.0"},
    "rf": {"total_codigos": 0, "gen": 0},
    "cal": {"bat": {"m": 1, "b": 0}},
}


async def _init_jsonb(conn):
    await conn.set_type_codec("jsonb", encoder=json.dumps,
                              decoder=json.loads, schema="pg_catalog")


@pytest.fixture
async def admin():
    conn = await asyncpg.connect(DSN_ADMIN)
    await _init_jsonb(conn)
    yield conn
    await conn.close()


@pytest.fixture
async def equipo(admin):
    """Un equipo OPERATIONAL en un barrio existente + uno huérfano (INVENTORY)."""
    await admin.execute(
        "DELETE FROM device WHERE mac = ANY($1::text[])", [MAC, MAC_HUERFANA])
    device_id = await admin.fetchval("""
        INSERT INTO device (serial, mac, type, status, tested,
                            board_model_id, board_seq,
                            neighborhood_id, latitude, longitude, installed_at)
        SELECT 'AV-' || $1, $1, 'COMMUNITY_ALARM', 'OPERATIONAL', true,
               (SELECT id FROM board_model ORDER BY id LIMIT 1), 9001,
               n.id, n.latitude + 0.0004, n.longitude + 0.0004, now()
          FROM neighborhood n ORDER BY n.id LIMIT 1
        RETURNING id""", MAC)
    await admin.execute("""
        INSERT INTO device (serial, mac, type, status, tested,
                            board_model_id, board_seq)
        VALUES ('AV-' || $1, $1, 'COMMUNITY_ALARM', 'INVENTORY', true,
                (SELECT id FROM board_model ORDER BY id LIMIT 1), 9002)""",
        MAC_HUERFANA)
    yield device_id
    await admin.execute("DELETE FROM event WHERE device_id = $1", device_id)
    await admin.execute(
        "DELETE FROM gtd.uplink_raw WHERE mac = ANY($1::text[])",
        [MAC, MAC_HUERFANA, MAC_FANTASMA])
    await admin.execute(
        "DELETE FROM device WHERE mac = ANY($1::text[])", [MAC, MAC_HUERFANA])


@pytest.fixture
async def repo():
    r = PgRepo(DSN)
    await r.start()
    yield r
    await r.close()


async def _estado(admin, device_id):
    return await admin.fetchrow(
        "SELECT * FROM device_state WHERE device_id = $1", device_id)


# ── 1-3: upsert_panel_state básico ──────────────────────────────────

async def test_unknown_device_no_explota(admin, equipo, repo):
    await repo.upsert_panel_state(MAC_FANTASMA, estado="online")   # sin excepción
    fila = await admin.fetchrow(
        "SELECT resultado FROM gtd.uplink_raw WHERE mac = $1 ORDER BY id DESC LIMIT 1",
        MAC_FANTASMA)
    assert fila["resultado"] == "unknown_device"


async def test_tele_completo_escribe_estado(admin, equipo, repo):
    await repo.upsert_panel_state(
        MAC, modo_energia="ACTIVE_240", alarma_mode="off", cfg_v=7, rf_gen=3,
        energia={"vbat": 12.60, "vpanel": 18.30, "vfuente": 13.80},
        ts=1700000000, tsq=0)
    ds = await _estado(admin, equipo)
    assert float(ds["vbat"]) == 12.60 and ds["cfg_v"] == 7 and ds["rf_gen"] == 3
    assert ds["power_mode"] == "ACTIVE_240"


async def test_null_no_toca(admin, equipo, repo):
    await repo.upsert_panel_state(MAC, energia={"vbat": 12.60})
    await repo.upsert_panel_state(MAC, alarma_mode="emergency")   # sin energía
    ds = await _estado(admin, equipo)
    assert float(ds["vbat"]) == 12.60          # NULL = no tocar
    assert ds["alarm_status"] == "emergency"


# ── 4-6: durmiendo, watchdog y el reloj del servidor ────────────────

async def test_durmiendo_fija_sleep_until_y_online_lo_limpia(admin, equipo, repo):
    despierta = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
    await repo.upsert_panel_state(MAC, estado="durmiendo", despierta=despierta)
    ds = await _estado(admin, equipo)
    assert ds["online"] is False and ds["sleep_until"] is not None

    await repo.upsert_panel_state(MAC, estado="online")
    ds = await _estado(admin, equipo)
    assert ds["online"] is True and ds["sleep_until"] is None


async def test_watchdog_no_pisa_last_seen(admin, equipo, repo):
    await repo.upsert_panel_state(MAC, estado="online")
    antes = (await _estado(admin, equipo))["last_seen"]
    assert antes is not None

    await repo.upsert_panel_state(MAC, estado="offline", seen=False)
    ds = await _estado(admin, equipo)
    assert ds["online"] is False
    assert ds["last_seen"] == antes            # el panel NO habló


async def test_last_seen_es_del_servidor(admin, equipo, repo):
    # Reloj del panel en 2017 (tsq=4, sin sync): last_seen NO puede salir de ahí.
    await repo.upsert_panel_state(MAC, estado="online", ts=1500000000, tsq=4)
    ds = await _estado(admin, equipo)
    assert ds["ts_device"].year == 2017 and ds["tsq"] == 4
    assert (datetime.now(timezone.utc) - ds["last_seen"]).total_seconds() < 60


# ── 7-9: insert_evento ──────────────────────────────────────────────

async def test_dedup_de_alarma(admin, equipo, repo):
    payload = {"mode": "emergency", "prev": "off", "origin": "rf", "tsq": 0}
    assert await repo.insert_evento(MAC, "alarma", payload,
                                    eid="it-1", ts=1700000000) is True
    assert await repo.insert_evento(MAC, "alarma", payload,
                                    eid="it-1", ts=1700000000) is False
    n = await admin.fetchval(
        "SELECT count(*) FROM event WHERE device_id = $1 AND external_id = 'it-1'",
        equipo)
    assert n == 1


async def test_desarme_va_al_dead_letter(admin, equipo, repo):
    payload = {"mode": "off", "prev": "emergency", "origin": "rf"}
    assert await repo.insert_evento(MAC, "alarma", payload, eid="it-2") is True
    fila = await admin.fetchrow(
        "SELECT resultado FROM gtd.uplink_raw WHERE mac = $1 AND eid = 'it-2'", MAC)
    assert fila["resultado"] == "desarme"
    n = await admin.fetchval(
        "SELECT count(*) FROM event WHERE device_id = $1 AND external_id = 'it-2'",
        equipo)
    assert n == 0


async def test_equipo_sin_barrio_es_orphan(admin, equipo, repo):
    payload = {"mode": "alert", "prev": "off", "origin": "rf"}
    assert await repo.insert_evento(MAC_HUERFANA, "alarma", payload, eid="it-3") is True
    fila = await admin.fetchrow(
        "SELECT resultado FROM gtd.uplink_raw WHERE mac = $1 AND eid = 'it-3'",
        MAC_HUERFANA)
    assert fila["resultado"] == "orphan"


# ── 10-12: el ciclo de bajada ───────────────────────────────────────

async def test_ciclo_de_comando(admin, equipo, repo):
    cid = await admin.fetchval(
        "SELECT gtd.enqueue_command($1, 'estado', '{}'::jsonb)", equipo)
    pendientes = await repo.fetch_pending_commands(MAC)
    assert [c["cid"] for c in pendientes] == [cid]

    await repo.mark_command_sent(cid)
    await repo.confirm_command(cid, res="ok", det="estado ok")
    fila = await admin.fetchrow(
        "SELECT estado, detalle FROM gtd.commands WHERE cid = $1", cid)
    assert fila["estado"] == "ok" and fila["detalle"] == "estado ok"


async def test_fetch_pending_macs(admin, equipo, repo):
    await admin.fetchval("SELECT gtd.enqueue_command($1, 'estado', '{}'::jsonb)", equipo)
    await repo.upsert_config_espejo(MAC, 1, ESPEJO_MINIMO)
    await admin.fetchval("SELECT gtd.publish_config($1, '{}'::jsonb)", equipo)

    alarms = await asyncpg.connect(DSN)
    try:
        filas = await alarms.fetch("SELECT mac, canal FROM gtd.fetch_pending_macs()")
    finally:
        await alarms.close()
    pares = {(f["mac"], f["canal"]) for f in filas}
    assert (MAC, "gtd_commands") in pares and (MAC, "gtd_config") in pares


async def test_cfg_failed_y_republicacion(admin, equipo, repo):
    # Sin espejo no hay patch: la web rechaza en vez de adivinar.
    with pytest.raises(asyncpg.PostgresError):
        await admin.fetchval("SELECT gtd.publish_config($1, '{}'::jsonb)",
                             await admin.fetchval(
                                 "SELECT id FROM device WHERE mac = $1", MAC_HUERFANA))

    await repo.upsert_config_espejo(MAC, 1, ESPEJO_MINIMO)
    cfg_v = await admin.fetchval("SELECT gtd.publish_config($1, '{}'::jsonb)", equipo)

    await repo.mark_config_failed(MAC, cfg_v, "payload 1180 B > 1024")
    fila = await admin.fetchrow(
        "SELECT estado, detalle FROM gtd.panel_config WHERE mac = $1", MAC)
    assert fila["estado"] == "failed" and "1024" in fila["detalle"]

    cfg_v2 = await admin.fetchval("SELECT gtd.publish_config($1, '{}'::jsonb)", equipo)
    fila = await admin.fetchrow(
        "SELECT estado, detalle, cfg_v FROM gtd.panel_config WHERE mac = $1", MAC)
    assert fila["estado"] == "pending" and fila["detalle"] is None
    assert fila["cfg_v"] == cfg_v2 > cfg_v


# ── 13-14: factory y reconciliación ─────────────────────────────────

async def test_factory_marca_stale(admin, equipo, repo):
    await repo.upsert_config_espejo(MAC, 1, ESPEJO_MINIMO)
    cfg_v = await admin.fetchval("SELECT gtd.publish_config($1, '{}'::jsonb)", equipo)
    await repo.mark_config_sent(MAC, cfg_v)

    await repo.upsert_panel_state(MAC, cfg_v=0)    # el panel volvió de fábrica
    estado = await admin.fetchval(
        "SELECT estado FROM gtd.panel_config WHERE mac = $1", MAC)
    assert estado == "stale"


async def test_cfg_v_reportada_aplica(admin, equipo, repo):
    await repo.upsert_config_espejo(MAC, 1, ESPEJO_MINIMO)
    cfg_v = await admin.fetchval("SELECT gtd.publish_config($1, '{}'::jsonb)", equipo)
    await repo.mark_config_sent(MAC, cfg_v)

    await repo.upsert_panel_state(MAC, cfg_v=cfg_v)   # el panel la reporta
    estado = await admin.fetchval(
        "SELECT estado FROM gtd.panel_config WHERE mac = $1", MAC)
    assert estado == "applied"


# ── 15-16: el contrato lo impone el motor ───────────────────────────

async def test_cps_alarms_sin_dml_directo(equipo):
    alarms = await asyncpg.connect(DSN)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await alarms.execute(
                "INSERT INTO device_state (device_id, online) VALUES ($1, true)",
                equipo)
    finally:
        await alarms.close()


async def test_cps_alarms_no_encola_comandos(equipo):
    alarms = await asyncpg.connect(DSN)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await alarms.fetchval(
                "SELECT gtd.enqueue_command($1, 'estado', '{}'::jsonb)", equipo)
    finally:
        await alarms.close()
