"""La BAJADA: lo que la app vieja lee.

Estos tests son el guardián de un contrato que NO está en ningún doc oficial:
sale de leer `main.dart` de la app instalada y la base de producción. Si alguno
falla, la app deja de mostrar algo — y lo va a hacer en silencio, que es lo
peligroso.
"""

from datetime import datetime, timezone

import pytest

from gtd.legacy.espejo import (
    ACTIVADA, Espejo, Snapshot, clave_historial, entrada_historial, tarjeta,
)
from gtd.legacy.proyector import proyectar_clientes, proyectar_evento


def snap(**kw) -> Snapshot:
    base = dict(
        marcador="CENTRALVECINAL05", estado=ACTIVADA, event_id=14,
        usuario="Santiago", telefono="3875372490", direccion="Mza 17-B casa 24",
        modoalarma="Ladrón", gps_lat=-24.233, gps_lng=-64.865,
        creado=datetime(2026, 8, 7, 13, 17, 51, tzinfo=timezone.utc),
    )
    base.update(kw)
    return Snapshot(**base)


class RtdbFalsa:
    def __init__(self):
        self.escrito: dict[str, object] = {}
        self.puts: list[str] = []
        self.escrituras = 0                     # cuántas veces se tocó la red

    async def patch(self, path, doc):
        self.escrito.setdefault(path, {})
        self.escrito[path].update(doc)          # type: ignore[union-attr]
        self.escrituras += 1

    async def put(self, path, doc):
        self.escrito[path] = doc
        self.puts.append(path)
        self.escrituras += 1

    async def delete(self, path):
        self.escrito.pop(path, None)

    async def get(self, path, **p):
        return self.escrito.get(path)


# ── El formato de la clave del historial ────────────────────────────────────

def test_clave_historial_no_es_iso8601():
    """DD-MM-YYYY, el día PRIMERO. La app lo parsea con un regex explícito y,
    si no matchea, cae en DateTime.now(): un formato ISO haría que todo el
    historial se muestre con la hora actual."""
    assert clave_historial(
        datetime(2026, 2, 24, 13, 17, 51, tzinfo=timezone.utc),
    ) == "24-02-2026T13:17:51Z"


def test_clave_historial_normaliza_a_utc():
    from datetime import timedelta
    arg = timezone(timedelta(hours=-3))
    assert clave_historial(
        datetime(2026, 2, 24, 10, 17, 51, tzinfo=arg),
    ) == "24-02-2026T13:17:51Z"


# ── La tarjeta ──────────────────────────────────────────────────────────────

def test_tarjeta_usa_las_claves_que_lee_la_app():
    t = tarjeta(snap())
    assert set(t) == {"usuario", "telefono", "direccion", "modoalarma", "GPS"}
    assert set(t["GPS"]) == {"latitud", "longitud"}


def test_gps_va_como_string_con_6_decimales():
    """La app parsea texto (`_toDoubleClean`); es el mismo formato que ella
    misma publica al activar."""
    assert tarjeta(snap())["GPS"] == {
        "latitud": "-24.233000", "longitud": "-64.865000",
    }


def test_sin_gps_no_se_escribe_la_clave():
    assert "GPS" not in tarjeta(snap(gps_lat=None, gps_lng=None))


def test_el_historial_guarda_menos_que_la_tarjeta():
    assert set(entrada_historial(snap())) == {"usuario", "direccion", "modoalarma"}


# ── La proyección ───────────────────────────────────────────────────────────

async def test_activada_escribe_estado_tarjeta_e_historial():
    db = RtdbFalsa()
    await Espejo(db).proyectar(snap())
    assert db.escrito["CENTRALVECINAL05/DatosCentral/Estado"] == "Activada"
    assert db.escrito["CENTRALVECINAL05/Instrucciones/InstruccionesActivacion"]
    assert (
        "CENTRALVECINAL05/Instrucciones/Historial/07-08-2026T13:17:51Z"
        in db.escrito
    )


@pytest.mark.parametrize("estado", ["Conectada", "Desconectada"])
async def test_sin_emergencia_solo_se_escribe_el_estado(estado):
    """No se borra la tarjeta: la app solo la muestra con Estado=='Activada',
    y borrarla haría parpadear la pantalla de quien la está mirando."""
    db = RtdbFalsa()
    await Espejo(db).proyectar(snap(estado=estado, event_id=None))
    assert list(db.escrito) == ["CENTRALVECINAL05/DatosCentral/Estado"]


async def test_el_historial_no_se_duplica_por_reproyectar():
    """El barrido reproyecta lo mismo cada 30 s: si cada pasada agregara una
    entrada, el historial del vecino se llenaría de la misma emergencia."""
    db, e = RtdbFalsa(), Espejo(RtdbFalsa())
    e = Espejo(db)
    for _ in range(5):
        await e.proyectar(snap())
    entradas = [k for k in db.escrito if "Historial/" in k]
    assert len(entradas) == 1


async def test_el_barrido_no_reescribe_lo_que_no_cambio():
    """Visto en producción el 2026-08-07: el barrido cada 30 s hacía dos PUT de
    `Estado` por equipo aunque no hubiera cambiado nada — ~5.800 escrituras
    diarias al pedo, y el journal tapado de ruido justo cuando hay que leerlo."""
    db = RtdbFalsa()
    e = Espejo(db)
    await e.proyectar(snap(estado="Conectada", event_id=None))
    primeras = db.escrituras
    for _ in range(10):
        await e.proyectar(snap(estado="Conectada", event_id=None))
    assert db.escrituras == primeras


async def test_pero_un_cambio_real_si_se_escribe():
    db = RtdbFalsa()
    e = Espejo(db)
    await e.proyectar(snap(estado="Conectada", event_id=None))
    await e.proyectar(snap(estado="Conectada", event_id=None))
    antes = db.escrituras
    await e.proyectar(snap(estado="Desconectada", event_id=None))
    assert db.escrituras == antes + 1
    assert db.escrito["CENTRALVECINAL05/DatosCentral/Estado"] == "Desconectada"


async def test_reproyectar_la_misma_emergencia_no_reescribe_la_tarjeta():
    db = RtdbFalsa()
    e = Espejo(db)
    await e.proyectar(snap())
    antes = db.escrituras
    for _ in range(5):
        await e.proyectar(snap())
    assert db.escrituras == antes


async def test_un_evento_nuevo_si_agrega_entrada():
    db = RtdbFalsa()
    e = Espejo(db)
    await e.proyectar(snap())
    await e.proyectar(snap(
        event_id=15, creado=datetime(2026, 8, 7, 14, 0, 0, tzinfo=timezone.utc),
    ))
    assert len([k for k in db.escrito if "Historial/" in k]) == 2


# ── El push ─────────────────────────────────────────────────────────────────

class FcmFalso:
    def __init__(self):
        self.enviados = []

    async def avisar(self, topico, usuario, motivo):
        self.enviados.append((topico, usuario, motivo))
        return "fake/1"


class PuertaConSnapshot:
    def __init__(self, s):
        self._s = s

    async def snapshot(self, device_id):
        return self._s


async def test_el_push_sale_una_sola_vez_por_evento():
    db, fcm, avisados = RtdbFalsa(), FcmFalso(), set()
    p = PuertaConSnapshot(snap())
    for _ in range(4):
        await proyectar_evento(1, p, Espejo(db), fcm, avisados)
    assert len(fcm.enviados) == 1
    assert fcm.enviados[0] == ("CENTRALVECINAL05", "Santiago", "Ladrón")


async def test_sin_emergencia_no_hay_push():
    fcm, avisados = FcmFalso(), set()
    p = PuertaConSnapshot(snap(estado="Conectada", event_id=None))
    await proyectar_evento(1, p, Espejo(RtdbFalsa()), fcm, avisados)
    assert fcm.enviados == []


# ── El catálogo ClientesID ──────────────────────────────────────────────────

class PuertaConClientes:
    def __init__(self, filas):
        self._filas = filas

    async def clientes(self):
        return self._filas


async def test_clientes_id_usa_las_claves_de_la_app_vieja():
    db = RtdbFalsa()
    await proyectar_clientes(PuertaConClientes([{
        "dni": "44679351", "usuario": "Santiago", "telefono": "387",
        "direccion": "Mza 5", "marcador": "CENTRALVECINAL05",
        "suspension": "OFF", "cupo": 3, "familia": ["44679352", "44679353"],
    }]), db)

    nodo = db.escrito["ClientesID/44679351"]
    # Mayúscula inicial: así las lee UserData.fromMap.
    assert set(nodo) == {"Usuario", "Telefono", "Direccion", "Marcador", "Suspension"}
    assert db.escrito["ClientesID/44679351/familia"] == {
        "nuser": 3, "usuario1": "44679352", "usuario2": "44679353",
    }


async def test_el_nodo_del_cliente_se_escribe_con_merge():
    """`Inicio` lo escribe la propia app al loguearse y `ControlRF` quedó de la
    carga original: un PUT sobre el nodo los borraría."""
    db = RtdbFalsa()
    await proyectar_clientes(PuertaConClientes([{
        "dni": "1", "usuario": "A", "telefono": "", "direccion": "",
        "marcador": "CENTRALVECINAL05", "suspension": "OFF",
        "cupo": 1, "familia": [],
    }]), db)
    # familia SÍ va con PUT: una baja tiene que hacer desaparecer su usuarioN.
    assert db.puts == ["ClientesID/1/familia"]


async def test_hogar_suspendido_llega_como_suspension_on():
    """Es lo que corta el envío del lado de la app."""
    db = RtdbFalsa()
    await proyectar_clientes(PuertaConClientes([{
        "dni": "1", "usuario": "A", "telefono": "", "direccion": "",
        "marcador": "CENTRALVECINAL05", "suspension": "ON",
        "cupo": 1, "familia": [],
    }]), db)
    assert db.escrito["ClientesID/1"]["Suspension"] == "ON"
