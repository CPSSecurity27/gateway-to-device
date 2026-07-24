"""Canal `up` con `t:cfg_full` — el espejo de config del panel.

Payload real capturado de una placa AV-240AC4000110 (fw new_0_6_0) el 2026-07-24.
El GtD lo descartaba como "t desconocido": el contrato no lo tenía.

Lo delicado acá son las passwords de WiFi que viajan en `redes[].psw`.
"""

import json
import logging

import pytest

from gtd.db.repo import StubRepo
from gtd.domain import payloads
from gtd.domain.contract import Channel, UpType
from gtd.domain.models import UpCfgFull
from gtd.obs.logging import redact
from gtd.pipeline import uplink

MAC = "AV-240AC4000110"

# Estructura textual del payload real. La password es de juguete.
CFG_FULL = {
    "v": 1, "t": "cfg_full", "cfg_v": 3, "ts": 1784930360, "tsq": 0,
    "redes": [{"ssid": "Wokwi-GUEST", "psw": "secreto-de-wifi", "prio": 1, "bl_perm": False}],
    "modulos": {"ds3231": False, "eeprom": False, "supervisor": False, "rf": True, "eeprom_slot": 0},
    "hora": {"tz_offset_s": -10800},
    "tiempos": {"send_tele_s": 300},
    "mante": {"on": False},
    "alarma": {"autooff": {"suspicious": 120, "alert": 300, "emergency": 600,
                           "fire": 600, "medical": 600, "silent": 600, "panic": 900}},
    "cal": {"bat": {"m": 1.0, "b": 0.0}, "panel": {"m": 1.0, "b": 0.0},
            "fuente": {"m": 1.0, "b": 0.0}},
    "red_avanzada": {"roam_rssi": -72, "roam_delta": 10, "roam_cooldown_s": 300},
    "rf": {"total_codigos": 0, "gen": 7},
    "id": {"dev": MAC, "fw": "new_0_6_0"},
}


def _raw(**over) -> bytes:
    return json.dumps({**CFG_FULL, **over}).encode()


def test_cfg_full_ya_no_es_t_desconocido():
    """La regresión concreta: antes esto tiraba PayloadError."""
    model, doc = payloads.parse(Channel.UP, _raw())
    assert isinstance(model, UpCfgFull)
    assert model.cfg_v == 3
    assert doc["red_avanzada"]["roam_rssi"] == -72   # el doc crudo va entero a Postgres


def test_extrae_lo_que_el_gtd_indexa():
    model, _ = payloads.parse(Channel.UP, _raw())
    assert model.fw == "new_0_6_0"
    assert model.rf_gen == 7
    assert model.redes[0]["ssid"] == "Wokwi-GUEST"


def test_cfg_v_es_obligatorio():
    """Sin cfg_v no se puede arbitrar contra la versión guardada."""
    doc = {k: v for k, v in CFG_FULL.items() if k != "cfg_v"}
    with pytest.raises(payloads.PayloadError):
        payloads.parse(Channel.UP, json.dumps(doc).encode())


def test_cfg_full_es_el_unico_up_que_no_es_evento():
    """Es estado, no algo que pasó: va al espejo, no a la tabla de eventos."""
    assert UpType.CFG_FULL.value == "cfg_full"
    assert UpType.CFG_FULL.value in payloads._UP_MODELS


async def test_ruteo_va_al_espejo_y_no_a_eventos():
    repo = StubRepo()
    await uplink.handle(f"av/{MAC}/up", _raw(cfg_v=9), repo)
    assert repo.eventos == []   # no es un evento


async def test_la_password_de_wifi_no_llega_al_log(caplog):
    """Lo que no puede pasar: una psw en claro en el journal del servidor."""
    caplog.set_level(logging.DEBUG)
    await uplink.handle(f"av/{MAC}/up", _raw(), StubRepo())

    todo = " ".join(r.getMessage() for r in caplog.records)
    assert "secreto-de-wifi" not in todo
    assert "psw" not in todo


def test_redact_tapa_las_redes_anidadas():
    """redact() tiene que entrar en la lista `redes`, no solo en el nivel 1."""
    limpio = redact(CFG_FULL)
    assert limpio["redes"][0]["psw"] == "***"
    assert limpio["redes"][0]["ssid"] == "Wokwi-GUEST"   # lo no sensible se conserva
