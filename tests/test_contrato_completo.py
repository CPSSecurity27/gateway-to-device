"""Barrido exhaustivo del contrato: TODOS los slugs, TODOS los tipos.

Los otros tests cubren casos representativos. Este cubre el catálogo entero, que
es donde aparecen las divergencias con el firmware: alcanza con que alguien
agregue un modo de alarma allá y no acá para que el panel dispare una emergencia
que el GtD descarta en silencio.

Si el firmware suma un slug, acá falta —y este test falla— hasta reconciliar.
"""

import json

import pytest

from gtd.db.repo import StubRepo
from gtd.domain import payloads
from gtd.domain.contract import (
    AlarmaMode,
    AlarmaOrigin,
    Channel,
    CmdType,
    UpType,
)
from gtd.pipeline import uplink

MAC = "AV-240AC4000110"


def _up(**campos) -> bytes:
    return json.dumps({"v": 1, "ts": 1700000000, "tsq": 2, **campos}).encode()


# ── Modos de alarma ─────────────────────────────────────────────────

@pytest.mark.parametrize("modo", list(AlarmaMode))
def test_todos_los_modos_de_alarma_se_parsean(modo):
    model, _ = payloads.parse(Channel.UP, _up(
        t="alarma", eid=f"e-{modo.value}", mode=modo.value, prev="off", origin="rf",
    ))
    assert model.mode is modo


@pytest.mark.parametrize("modo", list(AlarmaMode))
def test_todos_los_modos_valen_como_estado_previo(modo):
    """`prev` recorre el mismo catálogo: una transición desde cualquier modo."""
    model, _ = payloads.parse(Channel.UP, _up(
        t="alarma", eid=f"p-{modo.value}", mode="off", prev=modo.value, origin="auto",
    ))
    assert model.prev is modo


@pytest.mark.parametrize("origen", list(AlarmaOrigin))
def test_todos_los_origenes_se_parsean(origen):
    model, _ = payloads.parse(Channel.UP, _up(
        t="alarma", eid=f"o-{origen.value}", mode="alert", prev="off", origin=origen.value,
    ))
    assert model.origin is origen


@pytest.mark.parametrize("modo", list(AlarmaMode))
async def test_toda_alarma_termina_como_evento(modo):
    """Ningún modo puede perderse en el camino al repo."""
    repo = StubRepo()
    await uplink.handle(f"av/{MAC}/up", _up(
        t="alarma", eid=f"ev-{modo.value}", mode=modo.value, prev="off", origin="rf",
    ), repo)
    assert len(repo.eventos) == 1
    assert repo.eventos[0]["tipo"] == "alarma"


def test_un_modo_inventado_se_rechaza():
    with pytest.raises(payloads.PayloadError):
        payloads.parse(Channel.UP, _up(
            t="alarma", eid="x", mode="apocalipsis", prev="off", origin="rf",
        ))


def test_un_origen_inventado_se_rechaza():
    with pytest.raises(payloads.PayloadError):
        payloads.parse(Channel.UP, _up(
            t="alarma", eid="x", mode="off", prev="off", origin="telepatia",
        ))


# ── Tipos del canal up ──────────────────────────────────────────────

_UP_MINIMO = {
    UpType.ALARMA: {"eid": "e1", "mode": "off", "prev": "off", "origin": "rf"},
    UpType.ACK: {"cid": "c1", "res": "ok"},
    UpType.SCAN: {"redes": [{"ssid": "X", "rssi": -60}]},
    UpType.OTA: {"estado": 2, "fw": "6.0.0"},
    UpType.CFG_FULL: {"cfg_v": 1},
}


@pytest.mark.parametrize("tipo", list(UpType))
def test_todo_tipo_de_up_tiene_modelo(tipo):
    """El mapa de payloads.py no puede quedar corto respecto del enum.

    Es exactamente la falla que tuvimos con cfg_full: estaba el mensaje, no el
    modelo, y el GtD lo descartaba como 't desconocido'.
    """
    assert tipo.value in payloads._UP_MODELS
    model, _ = payloads.parse(Channel.UP, _up(t=tipo.value, **_UP_MINIMO[tipo]))
    assert model is not None


@pytest.mark.parametrize("tipo", list(UpType))
async def test_ningun_up_conocido_se_descarta(tipo, caplog):
    repo = StubRepo()
    await uplink.handle(f"av/{MAC}/up", _up(t=tipo.value, **_UP_MINIMO[tipo]), repo)
    assert "descartado" not in caplog.text


# ── Canales ─────────────────────────────────────────────────────────

def test_los_canales_de_subida_son_exactamente_tres():
    from gtd.mqtt import topics
    assert set(topics.UPLINK_CHANNELS) == {Channel.STATUS, Channel.TELE, Channel.UP}


@pytest.mark.parametrize("canal", [Channel.CFG, Channel.CMD])
def test_los_canales_de_bajada_no_se_parsean_como_subida(canal):
    """cfg y cmd los PUBLICA el GtD; recibirlos sería un bucle."""
    from gtd.mqtt import topics
    assert topics.parse(f"av/{MAC}/{canal.value}") is None


# ── Comandos (S→D) ──────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", list(CmdType))
def test_todo_comando_tiene_slug_no_vacio(cmd):
    """El GtD los emite; un slug vacío o repetido rompería el parse del firmware."""
    assert cmd.value and cmd.value.strip() == cmd.value


def test_no_hay_slugs_de_comando_repetidos():
    valores = [c.value for c in CmdType]
    assert len(valores) == len(set(valores))


# ── Estados de presencia ────────────────────────────────────────────

@pytest.mark.parametrize("estado,online", [
    ("online", True), ("durmiendo", False), ("offline", False),
])
def test_los_tres_estados_de_presencia(estado, online):
    model, _ = payloads.parse(
        Channel.STATUS, json.dumps({"v": 1, "estado": estado, "ts": 1}).encode()
    )
    assert model.online is online


# ── Robustez: nada de esto puede tirar abajo el pipeline ────────────

@pytest.mark.parametrize("basura", [
    b"", b"{", b"no soy json", b"[]", b"null", b'"texto"', b"\xff\xfe\x00",
    b'{"v":99,"t":"alarma"}',            # esquema incompatible
    b'{"t":"alarma","eid":"x"}',         # sin v
    b'{"v":1,"t":"marciano"}',           # t desconocido
    b'{"v":1,"t":"alarma"}',             # alarma sin campos obligatorios
])
async def test_payload_roto_se_descarta_sin_romper_nada(basura, caplog):
    repo = StubRepo()
    await uplink.handle(f"av/{MAC}/up", basura, repo)      # no debe levantar
    assert repo.eventos == []


@pytest.mark.parametrize("topico", [
    "av/", "av", "otra/cosa/status", "av/MAC/inventado",
    "av/MAC/status/extra", "", "av//status",
])
async def test_topico_raro_no_rompe(topico):
    await uplink.handle(topico, b'{"v":1,"estado":"online"}', StubRepo())
