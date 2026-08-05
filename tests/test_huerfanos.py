"""Barrido de credenciales huérfanas.

Lo que importa acá es el rail de seguridad: el archivo tiene también el usuario
del propio GtD, y un barrido ingenuo dejaría el sistema entero sin puente.
"""

from gtd.provisioner.broker import RegistradorFalso
from gtd.provisioner.cola import ColaStub
from gtd.provisioner.huerfanos import barrer, usuarios_de_equipo

PASSWD = """\
gateway:$7$101$abc$def
AV-A842E38FCA6C:$7$101$ghi$jkl
AV-AABBCCDDEE01:$7$101$mno$pqr
monitor-interno:$7$101$stu$vwx
"""


# ── Qué se considera un equipo ──────────────────────────────────────

def test_solo_los_usuarios_con_forma_de_equipo_entran_al_barrido():
    """`gateway` y cualquier cuenta de servicio quedan afuera. Borrarlas sería
    dejar el sistema sin puente."""
    assert usuarios_de_equipo(PASSWD) == {"AV-A842E38FCA6C", "AV-AABBCCDDEE01"}


def test_ignora_vacias_comentarios_y_lineas_sin_dos_puntos():
    texto = "\n# un comentario\n\nAV-A842E38FCA6C:hash\nbasura\n"
    assert usuarios_de_equipo(texto) == {"AV-A842E38FCA6C"}


def test_un_serial_mal_formado_no_cuenta_como_equipo():
    texto = "AV-NOESUNAMAC:hash\nav-a842e38fca6c:hash\nAV-A842E38FCA6C:hash\n"
    # Solo el canónico: 'AV-' + 12 hex MAYÚSCULAS.
    assert usuarios_de_equipo(texto) == {"AV-A842E38FCA6C"}


# ── El barrido ──────────────────────────────────────────────────────

async def test_revoca_lo_que_no_tiene_equipo(tmp_path):
    archivo = tmp_path / "gtd.passwd"
    archivo.write_text(PASSWD, encoding="utf-8")

    cola = ColaStub()
    cola.seriales = {"AV-A842E38FCA6C"}          # el otro se borró al fallar el alta
    reg = RegistradorFalso()

    assert await barrer(cola, reg, archivo) == ["AV-AABBCCDDEE01"]
    assert reg.llamadas == [["revoke", "AABBCCDDEE01", "--no-reload"]]
    assert reg.recargas == 1


async def test_si_no_sobra_nada_no_toca_el_broker(tmp_path):
    archivo = tmp_path / "gtd.passwd"
    archivo.write_text(PASSWD, encoding="utf-8")

    cola = ColaStub()
    cola.seriales = {"AV-A842E38FCA6C", "AV-AABBCCDDEE01"}
    reg = RegistradorFalso()

    assert await barrer(cola, reg, archivo) == []
    assert reg.llamadas == [] and reg.recargas == 0


async def test_en_seco_informa_pero_no_revoca(tmp_path):
    archivo = tmp_path / "gtd.passwd"
    archivo.write_text(PASSWD, encoding="utf-8")

    cola = ColaStub()
    reg = RegistradorFalso()

    assert await barrer(cola, reg, archivo, aplicar=False) == [
        "AV-A842E38FCA6C", "AV-AABBCCDDEE01",
    ]
    assert reg.llamadas == []


async def test_una_revocacion_que_falla_no_corta_el_barrido(tmp_path):
    archivo = tmp_path / "gtd.passwd"
    archivo.write_text(PASSWD, encoding="utf-8")

    cola = ColaStub()
    reg = RegistradorFalso(falla_en={"A842E38FCA6C"})

    assert await barrer(cola, reg, archivo) == ["AV-AABBCCDDEE01"]
    assert len(reg.llamadas) == 2


async def test_sin_archivo_no_rompe(tmp_path):
    cola = ColaStub()
    reg = RegistradorFalso()
    assert await barrer(cola, reg, tmp_path / "no-existe") == []
    assert reg.llamadas == []
