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

    async def confirm_config(self, mac, cfg_v, *, res="ok", det=None):
        self.llamadas.append(("confirm_cfg", mac, cfg_v, res, det))

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


async def test_ack_de_cfg_va_por_confirm_config():
    """El ack de una cfg NO trae cid: sin esto cae en el dead letter."""
    repo = RepoEspia()
    await uplink.handle(f"av/{DEVICE_ID}/up",
                        _msg(t="ack", cfg_v=7, res="ok"), repo)
    confirmaciones = [c for c in repo.llamadas if c[0] == "confirm_cfg"]
    assert confirmaciones == [("confirm_cfg", MAC, 7, "ok", None)]
    # Y NO se lo trata como ack de comando: no hay cid que correlacionar.
    assert [c for c in repo.llamadas if c[0] == "confirm"] == []


async def test_ack_de_cmd_sigue_yendo_por_confirm_command():
    repo = RepoEspia()
    await uplink.handle(f"av/{DEVICE_ID}/up",
                        _msg(t="ack", cid="c-42", res="ok"), repo)
    assert [c for c in repo.llamadas if c[0] == "confirm_cfg"] == []
    assert [c for c in repo.llamadas if c[0] == "confirm"] == [
        ("confirm", "c-42", "ok")
    ]


# ── Telemetría completa ─────────────────────────────────────────────
# El panel manda mucho más que voltajes: red, rtc, módulos, ota, contadores
# RF, sueño y colas. Todo eso llegaba y se tiraba, y sin ello no se puede
# responder por qué un equipo se cae — "se reconecta 40 veces con rssi -85"
# es la respuesta, y estaba viajando desde siempre.

TELE = dict(
    energia={"modo": "ACTIVE_240", "vbat": 12.6, "vpanel": 13.9, "vfuente": 0.0},
    red={"ssid": "CasaX", "ip": "192.168.1.7", "rssi": -61, "recon": 3,
         "ping_fail": 0, "wdt": 0},
    rtc={"q": 0, "sync_hace_s": 120, "ds3231": True, "ntp_boot": True},
    modulos={"supervisor": False},
    ota={"estado": 0, "ultimo": 0},
    rf={"rx": 123, "dec": 50, "desc": 2, "lim": 0},
    sueno={"despierta": 0, "motivo": 0},
    colas={"admin_drops": 0, "mqtt_out_drops": 0},
    cfg_v=13,
)


async def test_la_tele_pasa_el_bloque_de_red_aparte():
    """`red` va suelto porque ssid, ip y rssi tienen columna propia: son
    preguntas de FLOTA y eso no se responde leyendo un JSONB fila por fila."""
    repo = RepoEspia()
    await uplink.handle(f"av/{DEVICE_ID}/tele", _msg(**TELE), repo)

    kw = [c for c in repo.llamadas if c[0] == "state"][-1][2]
    assert kw["red"]["ssid"] == "CasaX"
    assert kw["red"]["ip"] == "192.168.1.7"
    assert kw["red"]["rssi"] == -61


async def test_la_tele_pasa_el_resto_del_snapshot_completo():
    repo = RepoEspia()
    await uplink.handle(f"av/{DEVICE_ID}/tele", _msg(**TELE), repo)

    tele = [c for c in repo.llamadas if c[0] == "state"][-1][2]["tele"]
    assert set(tele) == {"rtc", "modulos", "ota", "rf", "sueno", "colas"}
    assert tele["rf"]["desc"] == 2
    # `red` NO se duplica adentro: tiene columnas propias.
    assert "red" not in tele
    # `energia` tampoco: vbat/vpanel/vfuente ya son columnas.
    assert "energia" not in tele


async def test_una_tele_sin_secciones_no_inventa_claves():
    """Un firmware viejo que no manda `rf` no tiene que dejar un `rf: null` en
    la ficha: la sección simplemente no está."""
    repo = RepoEspia()
    await uplink.handle(f"av/{DEVICE_ID}/tele",
                        _msg(energia={"modo": "ACTIVE_240"}), repo)

    kw = [c for c in repo.llamadas if c[0] == "state"][-1][2]
    assert kw["tele"] == {}
    assert kw["red"] is None
