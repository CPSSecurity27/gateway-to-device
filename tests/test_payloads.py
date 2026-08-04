"""Guardián del contrato firmware↔GtD.

Si el firmware cambia un slug o un campo obligatorio y no se actualiza el
dominio, estos tests fallan. No dejar que el contrato divague en silencio.
"""

import pytest

from gtd.domain import payloads
from gtd.domain.contract import AlarmaMode, AlarmaOrigin, Channel
from gtd.domain.models import UpAlarma


def test_up_alarma_rf():
    raw = (
        '{"v":1,"t":"alarma","eid":"a1b2-5","mode":"emergency","prev":"off",'
        '"origin":"rf","dni":12345678,"codigos":2,"ts":1700000000,"tsq":2}'
    )
    model, doc = payloads.parse(Channel.UP, raw)
    assert isinstance(model, UpAlarma)
    assert model.eid == "a1b2-5"
    assert model.mode is AlarmaMode.EMERGENCY
    assert model.prev is AlarmaMode.OFF
    assert model.origin is AlarmaOrigin.RF
    assert model.dni == 12345678
    assert model.cid is None
    assert doc["codigos"] == 2   # el doc crudo se preserva íntegro para Postgres


def test_up_alarma_mqtt_lleva_cid():
    raw = (
        '{"v":1,"t":"alarma","eid":"a1b2-6","mode":"off","prev":"emergency",'
        '"origin":"mqtt","cid":"cmd-xyz","ts":1700000001,"tsq":2}'
    )
    model, _ = payloads.parse(Channel.UP, raw)
    assert model.origin is AlarmaOrigin.MQTT
    assert model.cid == "cmd-xyz"


def test_esquema_incompatible_se_rechaza():
    with pytest.raises(payloads.PayloadError):
        payloads.parse(Channel.UP, '{"v":99,"t":"alarma","eid":"x"}')


def test_up_con_t_desconocido():
    with pytest.raises(payloads.PayloadError):
        payloads.parse(Channel.UP, '{"v":1,"t":"marciano"}')


def test_up_alarma_sin_eid_es_malformado():
    with pytest.raises(payloads.PayloadError):
        payloads.parse(Channel.UP, '{"v":1,"t":"alarma","mode":"off","prev":"off","origin":"rf"}')


def test_status_online():
    raw = '{"v":1,"estado":"online","fw":"6.0.0","modo":"ACTIVE_240","ts":1700000000,"tsq":2}'
    model, _ = payloads.parse(Channel.STATUS, raw)
    assert model.online is True
    assert model.fw == "6.0.0"


def test_json_basura():
    with pytest.raises(payloads.PayloadError):
        payloads.parse(Channel.STATUS, "no soy json {")


# ── RF2/RF6: los cuatro tipos que antes se descartaban (doc 06 §3.f) ─
# Formas EXACTAS del firmware: task_mqtt.c l.992-1006 (rf_rx), l.324-327
# (rf_rx_end), l.363-374 (audit), l.390-411 (audit_detalle).

def _up(**campos) -> str:
    import json
    return json.dumps({"v": 1, "ts": 1700000000, "tsq": 2, **campos})


def test_rf_rx_conocido_y_desconocido():
    m, _ = payloads.parse(Channel.UP, _up(t="rf_rx", code=12345678901234, known=True,
                                          dni=30111222, pos=1))
    assert m.code == 12345678901234 and m.known and m.dni == 30111222 and m.pos == 1
    m, _ = payloads.parse(Channel.UP, _up(t="rf_rx", code=99, known=False))
    assert not m.known and m.dni is None


def test_rf_rx_end():
    m, _ = payloads.parse(Channel.UP, _up(t="rf_rx_end", motivo="timeout"))
    assert m.motivo == "timeout"


def test_audit_lote():
    m, _ = payloads.parse(Channel.UP, _up(t="audit", start=0, next=0xFFFF,
                                          lote=[{"dni": 30111222, "n": 2, "hash": 123456}]))
    assert m.next == 0xFFFF and m.lote[0]["dni"] == 30111222


def test_audit_detalle():
    m, _ = payloads.parse(Channel.UP, _up(t="audit_detalle",
        clientes=[{"dni": 30111222, "codigos": [{"pos": 0, "code": 111}]}]))
    assert m.clientes[0]["codigos"][0]["code"] == 111
