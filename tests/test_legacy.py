"""Puente con la app VIEJA de vecinos.

El contrato de entrada no es negociable: lo fija un APK ya distribuido
(`App_Alarma` v4.0.0+4, `_sendMqttInternal` en lib/main.dart). Estos tests son
el guardián de que el adaptador siga entendiendo exactamente eso.
"""

import pytest

from gtd.legacy.freno import Freno
from gtd.legacy.mensaje import MensajeInvalido, parse
from gtd.legacy.puerta import OK, PuertaStub, Resultado
from gtd.legacy.servicio import procesar


# ── El mensaje tal como lo publica la app ────────────────────────────────────

def test_mensaje_real_de_la_app():
    # Verbatim del formato de _sendMqttInternal: coordenadas como STRING con 6
    # decimales, longitud primero.
    raw = (
        '{"cliente_id":"44679351","modo_a":"cps003",'
        '"gps":{"longitud":"-64.865000","latitud":"-24.233000"}}'
    )
    a = parse(raw)
    assert a.dni == "44679351"
    assert a.modo == "cps003"
    assert a.lat == pytest.approx(-24.233)
    assert a.lng == pytest.approx(-64.865)
    assert not a.es_desactivacion


def test_sin_gps_es_valido():
    """Si no hay fix en 2 s la app publica SIN el campo gps. No es un error."""
    a = parse('{"cliente_id":"44679351","modo_a":"cps001"}')
    assert a.lat is None and a.lng is None


def test_cps999_es_desactivacion():
    assert parse('{"cliente_id":"1","modo_a":"cps999"}').es_desactivacion


def test_gps_cero_se_descarta():
    """0,0 es el 'sin fix' clásico, no un punto en el golfo de Guinea."""
    a = parse('{"cliente_id":"1","modo_a":"cps001","gps":{"latitud":"0","longitud":"0"}}')
    assert a.lat is None and a.lng is None


def test_media_coordenada_no_es_posicion():
    a = parse('{"cliente_id":"1","modo_a":"cps001","gps":{"latitud":"-24.2"}}')
    assert a.lat is None and a.lng is None


def test_gps_ilegible_no_invalida_la_activacion():
    """Preferimos una alarma sin ubicación antes que ninguna alarma."""
    a = parse('{"cliente_id":"1","modo_a":"cps001","gps":{"latitud":"x","longitud":"y"}}')
    assert a.dni == "1"
    assert a.lat is None


@pytest.mark.parametrize("raw", [
    "no soy json",
    "[]",
    '{"modo_a":"cps001"}',
    '{"cliente_id":"44679351"}',
    '{"cliente_id":"","modo_a":"cps001"}',
])
def test_payloads_invalidos(raw):
    with pytest.raises(MensajeInvalido):
        parse(raw)


def test_el_modo_no_se_valida_aca():
    """Quién decide si un modo existe es la base, en un solo lugar."""
    assert parse('{"cliente_id":"1","modo_a":"cps042"}').modo == "cps042"


# ── El freno ────────────────────────────────────────────────────────────────

def test_freno_por_dni():
    t = [0.0]
    f = Freno(por_dni_s=3.0, ahora=lambda: t[0])
    assert f.permite("111") is None
    t[0] = 1.0
    assert f.permite("111") == "freno_por_dni"
    assert f.permite("222") is None          # otro DNI no se ve afectado
    t[0] = 4.5
    assert f.permite("111") is None


def test_el_desarme_nunca_se_frena():
    """Una sirena que no se puede apagar es peor que una que suena de más."""
    t = [0.0]
    f = Freno(por_dni_s=3.0, global_por_min=1, ahora=lambda: t[0])
    assert f.permite("111") is None
    for _ in range(50):
        assert f.permite("111", es_desactivacion=True) is None


def test_freno_global_ataja_el_barrido_de_dnis():
    t = [0.0]
    f = Freno(por_dni_s=3.0, global_por_min=3, ahora=lambda: t[0])
    assert f.permite("1") is None
    assert f.permite("2") is None
    assert f.permite("3") is None
    assert f.permite("4") == "freno_global"   # DNI distinto, pasa el freno por DNI
    t[0] = 61.0                                # la ventana de 60 s se vació
    assert f.permite("5") is None


# ── El bucle ────────────────────────────────────────────────────────────────

class PuertaQueFalla:
    async def start(self): ...
    async def close(self): ...
    async def activar(self, a):
        raise RuntimeError("la base se cayó")


class PuertaQueRechaza:
    async def start(self): ...
    async def close(self): ...
    async def activar(self, a):
        return Resultado(cid=None, resultado="dni_desconocido")


async def test_procesar_camino_feliz():
    p, f = PuertaStub(), Freno()
    r = await procesar('{"cliente_id":"44679351","modo_a":"cps001"}', p, f)
    assert r == OK
    assert p.pedidos[0].dni == "44679351"


async def test_un_json_roto_no_llega_a_la_base():
    p, f = PuertaStub(), Freno()
    assert await procesar("{{{", p, f) == "malformado"
    assert p.pedidos == []


async def test_la_base_caida_no_tumba_el_proceso():
    """Este servicio es lo único que le queda a esos vecinos: no puede morirse."""
    assert await procesar(
        '{"cliente_id":"1","modo_a":"cps001"}', PuertaQueFalla(), Freno(),
    ) == "error"


async def test_el_rechazo_se_propaga_con_su_motivo():
    r = await procesar(
        '{"cliente_id":"9","modo_a":"cps001"}', PuertaQueRechaza(), Freno(),
    )
    assert r == "dni_desconocido"


async def test_el_freno_corta_antes_de_pegarle_a_la_base():
    p, f = PuertaStub(), Freno(por_dni_s=999.0)
    await procesar('{"cliente_id":"1","modo_a":"cps001"}', p, f)
    r = await procesar('{"cliente_id":"1","modo_a":"cps001"}', p, f)
    assert r == "freno_por_dni"
    assert len(p.pedidos) == 1
