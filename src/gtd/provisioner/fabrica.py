"""De una MAC a las dos credenciales del portal, listas para guardar.

Junta las dos mitades que no se pueden separar: derivar (`portal.py`, necesita
los salts) y cifrar (`cifrado.py`, necesita la clave compartida con la web). Es
una clase y no dos funciones sueltas para que los secretos se carguen y se
validen UNA vez al arrancar, no en cada equipo de la tanda.
"""

from __future__ import annotations

import logging

from . import cifrado, portal

log = logging.getLogger("gtd.provisioner.fabrica")


class Fabricador:
    """Deriva y cifra. Los secretos entran por el constructor y no salen."""

    def __init__(self, salt_tec: str, salt_cps: str, cred_key: str) -> None:
        # Ninguna de las dos falla en silencio: sin salts válidos se imprimen
        # etiquetas que no abren nada, y sin clave no se puede guardar.
        portal.verificar_salts(salt_tec, salt_cps)
        self._salt_tec = salt_tec
        self._salt_cps = salt_cps
        self._clave = cifrado.cargar_clave(cred_key)
        log.info("Fabricador listo — salts validados contra el vector")

    def credenciales(self, mac: str) -> tuple[str, str]:
        """`(admin_enc, cps_enc)` para esa MAC.

        Las passwords en claro viven solo en las variables locales de este
        método: lo que sale ya está cifrado.
        """
        c = portal.derivar(mac, self._salt_tec, self._salt_cps)
        return (
            cifrado.cifrar(self._clave, c.pass_admin),
            cifrado.cifrar(self._clave, c.pass_cps),
        )


class FabricadorFalso:
    """Doble para test: cifrado de mentira, sin salts ni clave."""

    def __init__(self) -> None:
        self.macs: list[str] = []

    def credenciales(self, mac: str) -> tuple[str, str]:
        self.macs.append(mac)
        return (f"enc-admin-{mac}", f"enc-cps-{mac}")
