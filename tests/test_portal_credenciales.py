"""Credenciales del portal local: derivación, cifrado y el vector de verificación.

Los salts REALES no están acá ni pueden estar: son secretos de toda la flota y
el repo es lo primero que se filtra. Se prueba el ALGORITMO con salts de prueba
—los valores esperados salen de la implementación de referencia del doc del
firmware— y aparte que `verificar_salts` rechace lo que no corresponde.

Si tenés los salts reales en el entorno, el último test valida el vector de
verdad; si no, se saltea.
"""

import os

import pytest

from gtd.provisioner import cifrado, portal

SALT_TEC = "SALT-TEC-DE-PRUEBA"
SALT_CPS = "SALT-CPS-DE-PRUEBA"
MAC = "A8:42:E3:8F:CA:6C"


# ── Normalización de la MAC ─────────────────────────────────────────

def test_acepta_los_tres_formatos_de_mac():
    """`esptool` la escupe con `:`; la base la guarda pelada; el serial lleva AV-."""
    esperado = bytes.fromhex("A842E38FCA6C")
    assert portal.normalizar_mac("A8:42:E3:8F:CA:6C") == esperado
    assert portal.normalizar_mac("a842e38fca6c") == esperado
    assert portal.normalizar_mac("AV-A842E38FCA6C") == esperado
    assert portal.normalizar_mac("A8-42-E3-8F-CA-6C") == esperado


def test_una_mac_que_no_mide_6_bytes_es_un_error():
    with pytest.raises(ValueError, match="6 bytes"):
        portal.normalizar_mac("A842E38FCA")


# ── La MAC SoftAP ───────────────────────────────────────────────────

def test_la_softap_es_la_sta_mas_uno():
    assert portal.mac_softap(bytes.fromhex("A842E38FCA6C")).hex().upper() \
        == "A842E38FCA6D"


def test_el_mas_uno_arrastra_cuando_el_ultimo_byte_es_ff():
    """Es sobre los 48 bits, no sobre el último octeto. Con FF hay carry."""
    assert portal.mac_softap(bytes.fromhex("AABBCCDDEEFF")).hex().upper() \
        == "AABBCCDDEF00"


# ── La derivación ───────────────────────────────────────────────────

def test_deriva_todo_lo_que_va_en_la_etiqueta():
    c = portal.derivar(MAC, SALT_TEC, SALT_CPS)

    assert c.device_id == "AV-A842E38FCA6C"
    assert c.ssid_ap == "AlarmaVecinal-A842E38FCA6C"
    # AP abierto: T:nopass y sin campo P:. Con T:WPA varios teléfonos fallan.
    assert c.qr_wifi == "WIFI:S:AlarmaVecinal-A842E38FCA6C;T:nopass;;"
    assert c.pass_admin == "CA0A21"
    assert c.pass_cps == "E08413"


def test_los_dos_roles_dan_passwords_distintas():
    c = portal.derivar(MAC, SALT_TEC, SALT_CPS)
    assert c.pass_admin != c.pass_cps


def test_las_passwords_son_6_hex_mayusculas():
    for mac in ("A842E38FCA6C", "000000000001", "FFFFFFFFFFFE"):
        c = portal.derivar(mac, SALT_TEC, SALT_CPS)
        for p in (c.pass_admin, c.pass_cps):
            assert len(p) == 6
            assert all(ch in "0123456789ABCDEF" for ch in p)


def test_usar_la_sta_en_vez_de_la_softap_cambia_un_solo_caracter():
    """El error clásico, y por qué el vector de verificación no es opcional.

    El djb2 XOREA el último byte al final, así que el `+1` mueve un bit. Una
    etiqueta derivada de la MAC equivocada sale plausible y no abre nada.
    """
    sta = bytes.fromhex("A842E38FCA6C")
    bien = portal._password(SALT_TEC, portal.mac_softap(sta))
    mal = portal._password(SALT_TEC, sta)

    assert bien != mal
    distintos = sum(1 for a, b in zip(bien, mal) if a != b)
    assert distintos == 1, f"{bien} vs {mal}: se parecen demasiado como para no validar"


def test_es_deterministica():
    """Misma MAC + mismo salt = misma password. Por eso no hace falta guardarla
    para poder recalcularla."""
    a = portal.derivar(MAC, SALT_TEC, SALT_CPS)
    b = portal.derivar("a842e38fca6c", SALT_TEC, SALT_CPS)
    assert a == b


# ── El vector de verificación ───────────────────────────────────────

def test_salts_vacios_abortan():
    with pytest.raises(portal.SaltInvalido, match="Faltan"):
        portal.verificar_salts("", "")


def test_salts_equivocados_abortan():
    """Lo que evita imprimir una tanda entera con credenciales que no abren."""
    with pytest.raises(portal.SaltInvalido, match="vector de verificación"):
        portal.verificar_salts(SALT_TEC, SALT_CPS)


@pytest.mark.skipif(
    not (os.getenv("GTD_SALT_TEC") and os.getenv("GTD_SALT_CPS")),
    reason="los salts reales no están en el entorno",
)
def test_los_salts_reales_reproducen_el_vector():
    portal.verificar_salts(os.environ["GTD_SALT_TEC"], os.environ["GTD_SALT_CPS"])


# ── El cifrado ──────────────────────────────────────────────────────

CLAVE = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="   # 32 bytes en base64


def test_ida_y_vuelta():
    clave = cifrado.cargar_clave(CLAVE)
    assert cifrado.descifrar(clave, cifrado.cifrar(clave, "2B0C49")) == "2B0C49"


def test_dos_cifrados_del_mismo_texto_son_distintos():
    """Nonce nuevo por llamada: si no, dos equipos con la misma password se
    delatan mirando la base."""
    clave = cifrado.cargar_clave(CLAVE)
    assert cifrado.cifrar(clave, "2B0C49") != cifrado.cifrar(clave, "2B0C49")


def test_un_blob_alterado_falla_en_vez_de_devolver_basura():
    """GCM es AEAD: sin esto se imprimirían 6 caracteres de basura como si
    fueran una password."""
    clave = cifrado.cargar_clave(CLAVE)
    blob = cifrado.cifrar(clave, "2B0C49")
    roto = blob[:-2] + ("AA" if not blob.endswith("AA") else "BB")

    with pytest.raises(Exception):
        cifrado.descifrar(clave, roto)


def test_otra_clave_no_descifra():
    otra = cifrado.cargar_clave("BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBA=")
    blob = cifrado.cifrar(cifrado.cargar_clave(CLAVE), "2B0C49")
    with pytest.raises(Exception):
        cifrado.descifrar(otra, blob)


def test_una_clave_de_otro_tamano_se_rechaza_al_cargarla():
    """Que falle al arrancar y no al fabricar el primer equipo."""
    with pytest.raises(cifrado.ClaveInvalida, match="AES-256"):
        cifrado.cargar_clave("AAAA")


def test_clave_vacia_se_rechaza():
    with pytest.raises(cifrado.ClaveInvalida, match="GTD_CRED_KEY"):
        cifrado.cargar_clave("")
