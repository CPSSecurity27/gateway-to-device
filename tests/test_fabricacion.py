"""La op `manufacture`: registrar en el broker Y derivar las del portal.

Lo que se prueba es la POLÍTICA del alta atómica, que es donde están las
decisiones: que no se confirme antes del reload, que un reload fallido invalide
la fabricación, y que sin fabricador el alta falle en vez de guardar un equipo
sin credenciales.
"""

from gtd.provisioner.broker import RegistradorFalso
from gtd.provisioner.cola import ColaStub, Pendiente
from gtd.provisioner.fabrica import FabricadorFalso
from gtd.provisioner.servicio import drenar

MAC = "A842E38FCA6C"


def _fabricacion(id_=1, mac=MAC):
    return Pendiente(id_, mac, "manufacture")


# ── El camino feliz ─────────────────────────────────────────────────

async def test_fabricar_registra_en_el_broker_y_guarda_las_dos_credenciales():
    cola = ColaStub([_fabricacion()])
    reg, fab = RegistradorFalso(), FabricadorFalso()

    assert await drenar(cola, reg, fab) == 1

    # Para el broker es un alta normal.
    assert reg.llamadas == [[MAC, "--no-reload", "--no-probe"]]
    assert fab.macs == [MAC]

    (id_, res, admin, cps, det), = cola.manufacturas
    assert (id_, res, det) == (1, "ok", None)
    assert admin == f"enc-admin-{MAC}" and cps == f"enc-cps-{MAC}"


async def test_la_fabricacion_no_pasa_por_confirmar_provisioning():
    """Son dos funciones SQL distintas a propósito: `confirm_manufacture`
    escribe columnas que la otra no debería poder tocar nunca."""
    cola = ColaStub([_fabricacion()])
    await drenar(cola, RegistradorFalso(), FabricadorFalso())

    assert cola.confirmaciones == []
    assert len(cola.manufacturas) == 1


# ── El orden: reload ANTES de confirmar ─────────────────────────────

async def test_si_el_reload_falla_la_fabricacion_falla():
    """La web espera esta confirmación para responder el alta. Una credencial
    escrita en el archivo que el broker todavía no leyó es un equipo que NO
    puede conectarse: confirmarla como ok sería la mentira que el alta atómica
    vino a evitar."""
    class ReloadRoto(RegistradorFalso):
        async def recargar(self):
            self.recargas += 1
            return "error", "mosquitto no quedó activo"

    cola = ColaStub([_fabricacion()])
    await drenar(cola, ReloadRoto(), FabricadorFalso())

    (_, res, admin, cps, det), = cola.manufacturas
    assert res == "error"
    assert det and "no quedó activa" in det
    # Y no se guardan credenciales a medias.
    assert admin is None and cps is None


async def test_un_reload_roto_no_tumba_las_provisiones_sueltas():
    """Comportamiento preexistente: `provision` tiene botón de reintento y nadie
    está esperándola. No se cambia de arrastre."""
    class ReloadRoto(RegistradorFalso):
        async def recargar(self):
            self.recargas += 1
            return "error", "mosquitto no quedó activo"

    cola = ColaStub([Pendiente(1, MAC, "provision")])
    await drenar(cola, ReloadRoto(), FabricadorFalso())

    assert [c[1] for c in cola.confirmaciones] == ["ok"]


# ── Los modos de falla ──────────────────────────────────────────────

async def test_sin_fabricador_el_alta_falla_con_el_motivo():
    """Correr el provisioner sin los salts del portal es un error de
    configuración: tiene que decirlo, no guardar un equipo a medias."""
    cola = ColaStub([_fabricacion()])
    await drenar(cola, RegistradorFalso(), None)

    (_, res, admin, cps, det), = cola.manufacturas
    assert res == "error"
    assert det and "salts del portal" in det
    assert admin is None and cps is None


async def test_si_el_broker_falla_no_se_deriva_nada():
    cola = ColaStub([_fabricacion()])
    fab = FabricadorFalso()
    await drenar(cola, RegistradorFalso(falla_en={MAC}), fab)

    (_, res, admin, cps, _), = cola.manufacturas
    assert res == "error"
    assert admin is None and cps is None
    # Ni se intentó: sin credencial MQTT el equipo no sirve igual.
    assert fab.macs == []


async def test_si_la_derivacion_explota_la_fabricacion_falla():
    class FabricadorRoto:
        def credenciales(self, mac):
            raise RuntimeError("el salt no reproduce el vector")

    cola = ColaStub([_fabricacion()])
    await drenar(cola, RegistradorFalso(), FabricadorRoto())

    (_, res, _, _, det), = cola.manufacturas
    assert res == "error"
    assert det and "derivación del portal" in det


# ── Convivencia con el resto de la cola ─────────────────────────────

async def test_una_fabricacion_fallida_no_arrastra_al_resto_de_la_tanda():
    cola = ColaStub([
        _fabricacion(1, "AABBCCDDEE01"),
        _fabricacion(2, "AABBCCDDEE02"),
        Pendiente(3, "AABBCCDDEE03", "provision"),
    ])
    reg = RegistradorFalso(falla_en={"AABBCCDDEE02"})

    assert await drenar(cola, reg, FabricadorFalso()) == 3

    assert {i: r for i, r, _, _, _ in cola.manufacturas} == {1: "ok", 2: "error"}
    assert [c[1] for c in cola.confirmaciones] == ["ok"]
    # Sigue siendo un solo reload por tanda.
    assert reg.recargas == 1


async def test_el_orden_de_la_cola_se_respeta_con_fabricaciones():
    cola = ColaStub([
        _fabricacion(10, "AABBCCDDEE0A"),
        Pendiente(11, "AABBCCDDEE0B", "revoke"),
        _fabricacion(12, "AABBCCDDEE0C"),
    ])
    await drenar(cola, RegistradorFalso(), FabricadorFalso())

    assert [m[0] for m in cola.manufacturas] == [10, 12]
    assert [c[0] for c in cola.confirmaciones] == [11]
