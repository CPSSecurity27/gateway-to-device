"""Uplink v2: el estado viaja completo (durmiendo ≠ offline), el bool del
dedup se usa, y el cfg_full va al histórico SIN passwords.
"""

import json

import pytest

from gtd.db.repo import StubRepo
from gtd.pipeline import presencia, uplink

DEVICE_ID = "AV-240AC4000110"
MAC = "240AC4000110"


class RepoEspia(StubRepo):
    def __init__(self):
        super().__init__()
        self.llamadas: list[tuple] = []

    async def upsert_panel_state(self, mac, **kw):
        self.llamadas.append(("state", mac, kw))
        await super().upsert_panel_state(mac, **kw)

    async def confirm_command(self, cid, *, res=None, det=None):
        self.llamadas.append(("confirm", cid, res))

    async def insert_evento(self, mac, tipo, payload, *, eid=None, ts=None):
        self.llamadas.append(("evento", mac, tipo, payload))
        return await super().insert_evento(mac, tipo, payload, eid=eid, ts=ts)


def _msg(**campos) -> bytes:
    return json.dumps({"v": 1, "ts": 1700000000, "tsq": 2, **campos}).encode()


@pytest.fixture(autouse=True)
def _presencia_limpia():
    presencia.reiniciar()
    yield
    presencia.reiniciar()


async def test_status_durmiendo_pasa_estado_y_despierta():
    repo = RepoEspia()
    await uplink.handle(f"av/{DEVICE_ID}/status",
                        _msg(estado="durmiendo", despierta=1700003600), repo)
    kw = [c for c in repo.llamadas if c[0] == "state"][-1][2]
    assert kw["estado"] == "durmiendo" and kw["despierta"] == 1700003600
    assert kw["ts"] == 1700000000 and kw["tsq"] == 2
    assert "online" not in kw and "last_seen" not in kw


async def test_alarma_duplicada_no_confirma_dos_veces():
    repo = RepoEspia()
    alarma = _msg(t="alarma", eid="b1-7", mode="emergency", prev="off",
                  origin="mqtt", cid="c-42")
    await uplink.handle(f"av/{DEVICE_ID}/up", alarma, repo)
    await uplink.handle(f"av/{DEVICE_ID}/up", alarma, repo)   # QoS1 redistribuye
    confirmaciones = [c for c in repo.llamadas if c[0] == "confirm"]
    assert len(confirmaciones) == 1


async def test_cfg_full_va_al_historico_sin_passwords():
    repo = RepoEspia()
    cfg = _msg(t="cfg_full", cfg_v=7,
               redes=[{"ssid": "Casa", "psw": "SECRETA", "prio": 1}])
    await uplink.handle(f"av/{DEVICE_ID}/up", cfg, repo)
    eventos = [c for c in repo.llamadas if c[0] == "evento" and c[2] == "cfg_full"]
    assert len(eventos) == 1
    assert eventos[0][3]["redes"][0]["psw"] == "***"      # redactado
    assert eventos[0][3]["redes"][0]["ssid"] == "Casa"    # el resto intacto
