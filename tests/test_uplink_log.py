"""El log de conexión de paneles.

Es la única señal operativa de "esta alarma apareció/se cayó" cuando el GtD corre
en el servidor, así que se testea como cualquier otra parte del contrato.
"""

import logging

import pytest

from gtd.db.repo import StubRepo
from gtd.pipeline import uplink

MAC = "AA:BB:CC:DD:EE:FF"


def _status(estado: str, **extra) -> bytes:
    campos = {"v": 1, "estado": estado, "ts": 1700000000, **extra}
    cuerpo = ",".join(
        f'"{k}":{v}' if isinstance(v, int) else f'"{k}":"{v}"'
        for k, v in campos.items()
    )
    return ("{" + cuerpo + "}").encode()


@pytest.fixture(autouse=True)
def _limpiar_estado():
    """El caché de transiciones es global: aislar cada test."""
    uplink._last_estado.clear()
    yield
    uplink._last_estado.clear()


async def test_primer_status_online_se_loguea(caplog):
    caplog.set_level(logging.INFO, logger="gtd.uplink")
    await uplink.handle(f"av/{MAC}/status", _status("online", modo="ACTIVE_240", fw="6.0.0"), StubRepo())

    (rec,) = [r for r in caplog.records if r.name == "gtd.uplink"]
    assert "panel ONLINE" in rec.message
    assert MAC in rec.message
    assert "modo=ACTIVE_240" in rec.message
    assert "fw=6.0.0" in rec.message
    assert rec.levelno == logging.INFO


async def test_status_repetido_no_repite_el_log(caplog):
    """status es retained: se reenvía en cada reconexión. Una línea, no N."""
    caplog.set_level(logging.INFO, logger="gtd.uplink")
    repo = StubRepo()
    for _ in range(3):
        await uplink.handle(f"av/{MAC}/status", _status("online", modo="ACTIVE_240"), repo)

    assert len([r for r in caplog.records if r.name == "gtd.uplink"]) == 1


async def test_offline_es_warning(caplog):
    caplog.set_level(logging.INFO, logger="gtd.uplink")
    repo = StubRepo()
    await uplink.handle(f"av/{MAC}/status", _status("online"), repo)
    caplog.clear()
    await uplink.handle(f"av/{MAC}/status", _status("offline", causa="lwt"), repo)

    (rec,) = [r for r in caplog.records if r.name == "gtd.uplink"]
    assert "panel OFFLINE" in rec.message
    assert "causa=lwt" in rec.message
    assert "antes=online" in rec.message
    assert rec.levelno == logging.WARNING


async def test_durmiendo_no_es_offline(caplog):
    """Un panel que duerme no está caído: INFO, no WARNING."""
    caplog.set_level(logging.INFO, logger="gtd.uplink")
    await uplink.handle(
        f"av/{MAC}/status", _status("durmiendo", despierta=1700003600), StubRepo()
    )

    (rec,) = [r for r in caplog.records if r.name == "gtd.uplink"]
    assert "DURMIENDO" in rec.message
    assert "despierta=1700003600" in rec.message
    assert rec.levelno == logging.INFO


async def test_paneles_distintos_no_se_pisan(caplog):
    caplog.set_level(logging.INFO, logger="gtd.uplink")
    repo = StubRepo()
    otra = "11:22:33:44:55:66"
    await uplink.handle(f"av/{MAC}/status", _status("online"), repo)
    await uplink.handle(f"av/{otra}/status", _status("online"), repo)

    msgs = [r.message for r in caplog.records if r.name == "gtd.uplink"]
    assert len(msgs) == 2
    assert any(MAC in m for m in msgs) and any(otra in m for m in msgs)


async def test_tele_no_dispara_log_de_conexion(caplog):
    """Solo status habla de conexión; la telemetría no debe ensuciar."""
    caplog.set_level(logging.INFO, logger="gtd.uplink")
    raw = b'{"v":1,"cfg_v":3,"energia":{"modo":"red"},"ts":1700000000}'
    await uplink.handle(f"av/{MAC}/tele", raw, StubRepo())

    assert [r for r in caplog.records if r.name == "gtd.uplink"] == []
