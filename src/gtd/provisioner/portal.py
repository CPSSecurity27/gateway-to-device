"""Credenciales del PORTAL LOCAL del equipo (AP abierto + portal web).

NO son las del broker. El equipo tiene dos juegos de credenciales y confundirlos
es el error clásico de esta parte:

    MQTT    equipo <-> broker.  HMAC-SHA256(SALT_MQTT, MAC **STA**), 96 bits.
            La deriva `provision-panel.sh`; acá no se toca.
    PORTAL  técnico <-> 192.168.4.1.  djb2_xor(salt_del_rol, MAC **SoftAP**),
            24 bits. Es esto.

Fuente: `AlarmaESP32V6/docs/provisioning_credenciales_ap_portal.md` y
`components/wifi_manager/wifi_manager.c:258-327`. Se implementa literal: si esto
diverge del firmware, la etiqueta sale con una clave que no abre el portal.

## Las dos trampas

1. **La MAC es la SoftAP, no la STA.** El SSID y la credencial MQTT usan la STA;
   las passwords del portal usan la SoftAP, que en ESP32 es la STA + 1 sobre los
   48 bits completos (no sobre el último octeto: con `FF` hay que arrastrar).

   Y no es un error que se note mirando: como el djb2 XOREA el último byte al
   final, usar la STA por equivocación da una password que difiere en **un solo
   carácter**. Se imprimiría una etiqueta plausible que no abre nada. Por eso el
   vector de verificación de abajo no es opcional.

   Verificado contra hardware real el 2026-08-04: la placa `A842E38FCA6C` abre
   el portal con la password derivada de `A842E38FCA6D`.

2. **El djb2 va acotado a 32 bits en cada paso.** En C desborda solo; en Python
   los enteros son infinitos y sin el mask explícito da otro número.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

M32 = 0xFFFFFFFF

# Vector de verificación, guardado como HASH y no en claro: son las passwords
# reales de la placa de desarrollo, y `pass_cps` es justamente la que el doc del
# firmware manda no exponer nunca. El hash prueba que los salts son los correctos
# sin que el repo revele ninguna de las dos.
#
# Mismo criterio que `provision-panel.sh`, que valida SALT_MQTT antes de derivar:
# un salt equivocado registra credenciales que parecen válidas y fallan recién
# cuando alguien está parado frente al equipo con la etiqueta en la mano.
KAT_MAC = "A842E38FCA6C"
KAT_SHA256 = "232c6ddefcd36fe7f51058160957640304b5b49dde16e0af70645c53514ed531"


class SaltInvalido(RuntimeError):
    """Los salts no reproducen el vector de verificación."""


@dataclass(frozen=True)
class CredencialesPortal:
    device_id: str        # AV-<12 hex> — el mismo string que el usuario MQTT
    mac_sta: str          # 12 hex mayúsculas
    mac_softap: str       # la de las passwords
    ssid_ap: str
    qr_wifi: str
    pass_admin: str       # rol TEC — va impresa en la etiqueta
    pass_cps: str         # rol CPS — JAMÁS se imprime


def normalizar_mac(mac: str) -> bytes:
    """Acepta `A8:42:E3:8F:CA:6C`, `A842E38FCA6C` o `AV-A842E38FCA6C`."""
    limpia = mac.replace(":", "").replace("-", "").replace(" ", "").upper()
    if limpia.startswith("AV"):
        limpia = limpia[2:]
    crudos = bytes.fromhex(limpia)
    if len(crudos) != 6:
        raise ValueError(f"la MAC debe ser de 6 bytes, vinieron {len(crudos)}")
    return crudos


def mac_softap(sta: bytes) -> bytes:
    """STA + 1. Sobre el entero de 48 bits: con el último byte en FF hay carry."""
    return (int.from_bytes(sta, "big") + 1).to_bytes(6, "big")


def _djb2_xor(salt: str, mac: bytes) -> int:
    """El hash del firmware. Cada paso acotado a 32 bits, como el uint32 en C."""
    h = 5381
    for c in salt.encode():
        h = (((h << 5) + h) & M32) ^ c
    for b in mac:
        h = (((h << 5) + h) & M32) ^ b
    return h & M32


def _password(salt: str, ap: bytes) -> str:
    """Los 24 bits bajos, 6 hex mayúsculas con ceros a la izquierda."""
    return "%06X" % (_djb2_xor(salt, ap) & 0xFFFFFF)


def derivar(mac: str, salt_tec: str, salt_cps: str) -> CredencialesPortal:
    """Todo lo que va en la etiqueta, a partir de la MAC STA."""
    sta = normalizar_mac(mac)
    ap = mac_softap(sta)
    hexid = sta.hex().upper()
    ssid = "AlarmaVecinal-" + hexid

    return CredencialesPortal(
        device_id="AV-" + hexid,
        mac_sta=hexid,
        mac_softap=ap.hex().upper(),
        ssid_ap=ssid,
        # AP ABIERTO: T:nopass y sin campo P:. Con `T:WPA` varios teléfonos
        # fallan la conexión. Los `;;` finales son parte del formato.
        qr_wifi=f"WIFI:S:{ssid};T:nopass;;",
        pass_admin=_password(salt_tec, ap),
        pass_cps=_password(salt_cps, ap),
    )


def verificar_salts(salt_tec: str, salt_cps: str) -> None:
    """Aborta si los salts no son los de producción.

    Se llama UNA vez al arrancar, antes de derivar nada. Sin esto, un salt
    equivocado no se manifiesta hasta que un técnico no puede entrar al equipo
    —a veces semanas después, con la etiqueta ya pegada— y para entonces nadie
    relaciona una cosa con la otra.
    """
    if not salt_tec or not salt_cps:
        raise SaltInvalido(
            "Faltan GTD_SALT_TEC y/o GTD_SALT_CPS: sin ellos no se pueden "
            "derivar las credenciales del portal.",
        )

    c = derivar(KAT_MAC, salt_tec, salt_cps)
    calculado = hashlib.sha256(
        f"{KAT_MAC}|{c.pass_admin}|{c.pass_cps}".encode(),
    ).hexdigest()

    if calculado != KAT_SHA256:
        raise SaltInvalido(
            "Los salts del portal NO reproducen el vector de verificación "
            f"(MAC {KAT_MAC}). O no son los de producción, o la derivación "
            "cambió en el firmware. NO se derivó nada.",
        )
