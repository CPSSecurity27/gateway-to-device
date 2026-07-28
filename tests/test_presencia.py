"""Presencia: reconexiones, silencio y el watchdog.

Motivado por datos reales: una placa sobre Starlink se desconectó 31 veces en un
día (14 por timeout de keepalive, 7 por takeover de sesión, 6 por error de
protocolo, 4 cierres limpios) y el GtD registró apenas unas pocas — en el takeover
el broker NO publica el LWT y el panel republica `online`, así que para el GtD
nunca se había ido.
"""

import json
import logging
import time

import pytest

from gtd.db.repo import StubRepo
from gtd.pipeline import presencia, uplink

MAC = "AV-A842E38FCA6C"
OTRA = "AV-240AC4000110"
GAP = 60.0


@pytest.fixture(autouse=True)
def _limpio():
    presencia.reiniciar()
    yield
    presencia.reiniciar()


def _status(estado="online", **extra) -> bytes:
    return json.dumps({"v": 1, "estado": estado, "ts": 1700000000, **extra}).encode()


def _envejecer(mac: str, segundos: float) -> None:
    """Simula que el panel viene callado, sin dormir el test de verdad."""
    presencia._paneles[mac].ultimo_msg -= segundos


# ── Clasificación del status ────────────────────────────────────────

def test_primera_vez():
    ev, antes = presencia.ver_status(MAC, "online", silencio=0, gap=GAP)
    assert ev is presencia.EventoStatus.PRIMERA_VEZ
    assert antes == "?"


def test_cambio_de_estado():
    presencia.ver_status(MAC, "online", silencio=0, gap=GAP)
    ev, antes = presencia.ver_status(MAC, "offline", silencio=1, gap=GAP)
    assert ev is presencia.EventoStatus.CAMBIO
    assert antes == "online"


def test_status_repetido_sin_silencio_no_es_reconexion():
    """Un retained reentregado, o un status periódico: no pasó nada."""
    presencia.ver_status(MAC, "online", silencio=0, gap=GAP)
    ev, _ = presencia.ver_status(MAC, "online", silencio=30, gap=GAP)
    assert ev is presencia.EventoStatus.REPETIDO


def test_status_repetido_tras_silencio_es_reconexion():
    """La firma de un takeover: mismo estado, pero el panel estuvo callado."""
    presencia.ver_status(MAC, "online", silencio=0, gap=GAP)
    ev, _ = presencia.ver_status(MAC, "online", silencio=63, gap=GAP)
    assert ev is presencia.EventoStatus.RECONEXION
    assert presencia.reconexiones(MAC) == 1


def test_las_reconexiones_se_acumulan():
    presencia.ver_status(MAC, "online", silencio=0, gap=GAP)
    for _ in range(5):
        presencia.ver_status(MAC, "online", silencio=90, gap=GAP)
    assert presencia.reconexiones(MAC) == 5
    assert presencia.reconexiones(OTRA) == 0   # no se mezclan entre paneles


def test_dormido_repetido_nunca_es_reconexion():
    """Un panel que duerme está callado por diseño: el silencio no significa nada."""
    presencia.ver_status(MAC, "durmiendo", silencio=0, gap=GAP, despierta=1)
    ev, _ = presencia.ver_status(MAC, "durmiendo", silencio=9999, gap=GAP, despierta=1)
    assert ev is presencia.EventoStatus.REPETIDO


def test_reiniciar_borra_todo():
    """Lo que corre cuando el GtD reconecta: los retained no son reconexiones."""
    presencia.ver_status(MAC, "online", silencio=0, gap=GAP)
    presencia.reiniciar()
    ev, _ = presencia.ver_status(MAC, "online", silencio=9999, gap=GAP)
    assert ev is presencia.EventoStatus.PRIMERA_VEZ
    assert presencia.reconexiones(MAC) == 0


# ── Watchdog de silencio ────────────────────────────────────────────

def test_panel_que_habla_no_esta_sin_senal():
    presencia.actividad(MAC)
    assert presencia.sin_senal(180) == []


def test_panel_callado_se_detecta():
    presencia.actividad(MAC)
    _envejecer(MAC, 200)
    vencidos = presencia.sin_senal(180)
    assert [m for m, _ in vencidos] == [MAC]
    assert vencidos[0][1] >= 200


def test_no_se_avisa_dos_veces():
    """Sin esto, el watchdog gritaría cada 30s por el mismo panel caído."""
    presencia.actividad(MAC)
    _envejecer(MAC, 200)
    assert len(presencia.sin_senal(180)) == 1
    assert presencia.sin_senal(180) == []


def test_un_corte_de_starlink_no_marca_caido():
    """Se midieron cortes reales de ~50s. El umbral de 180s no debe morder."""
    presencia.actividad(MAC)
    _envejecer(MAC, 50)
    assert presencia.sin_senal(180) == []


def test_panel_dormido_no_se_marca_caido():
    presencia.actividad(MAC)
    presencia.ver_status(MAC, "durmiendo", silencio=0, gap=GAP,
                         despierta=int(time.time()) + 3600)
    _envejecer(MAC, 999)
    assert presencia.sin_senal(180) == []


def test_dormido_que_no_desperto_si_se_marca():
    """Un panel que dijo 'me despierto a las 3' y a las 4 sigue mudo, falló."""
    presencia.actividad(MAC)
    presencia.ver_status(MAC, "durmiendo", silencio=0, gap=GAP,
                         despierta=int(time.time()) - 3600)
    _envejecer(MAC, 999)
    assert [m for m, _ in presencia.sin_senal(180)] == [MAC]


# ── Integración con el uplink ───────────────────────────────────────

async def test_el_uplink_loguea_la_reconexion(caplog):
    caplog.set_level(logging.INFO, logger="gtd.uplink")
    repo = StubRepo()
    await uplink.handle(f"av/{MAC}/status", _status(modo="ACTIVE_240"), repo)
    _envejecer(MAC, 120)
    caplog.clear()
    await uplink.handle(f"av/{MAC}/status", _status(modo="ACTIVE_240"), repo)

    msgs = [r.message for r in caplog.records if r.name == "gtd.uplink"]
    assert any("RECONECTÓ" in m for m in msgs)
    assert any("van 1" in m for m in msgs)


async def test_telemetria_no_dispara_reconexiones(caplog):
    """La tele va por otro canal y no lleva `estado`: no puede tocar la presencia."""
    caplog.set_level(logging.INFO, logger="gtd.uplink")
    repo = StubRepo()
    await uplink.handle(f"av/{MAC}/status", _status(), repo)
    caplog.clear()
    for _ in range(5):
        _envejecer(MAC, 120)
        await uplink.handle(f"av/{MAC}/tele",
                            b'{"v":1,"energia":{"modo":"ACTIVE_240"},"ts":1}', repo)

    assert not [r for r in caplog.records if "RECONECT" in r.message]


async def test_el_panel_que_vuelve_se_marca_online(caplog):
    caplog.set_level(logging.INFO, logger="gtd.uplink")
    repo = StubRepo()
    await uplink.handle(f"av/{MAC}/status", _status(), repo)
    _envejecer(MAC, 300)
    presencia.sin_senal(180)              # el watchdog lo da por caído
    caplog.clear()

    await uplink.handle(f"av/{MAC}/tele",
                        b'{"v":1,"energia":{"modo":"ACTIVE_240"},"ts":1}', repo)
    assert any("VOLVIÓ" in r.message for r in caplog.records)
    assert repo.panel_state[MAC]["online"] is True


async def test_payload_roto_cuenta_como_señal_de_vida():
    """Un panel que manda basura está vivo: no debe darse por caído."""
    repo = StubRepo()
    await uplink.handle(f"av/{MAC}/up", b"no soy json {", repo)
    assert presencia.sin_senal(180) == []
