"""Registro de la credencial en Mosquitto.

NO reimplementa la derivación HMAC: invoca `deploy/provision-panel.sh`, que ya
la hace bien (los 6 bytes crudos de la MAC, no el string hex) y —lo más
importante— VALIDA el salt contra un vector de verificación conocido antes de
derivar nada. Con un salt equivocado aborta sin registrar, en vez de cargar
credenciales que parecen válidas y fallan recién cuando el panel intenta
conectar, que es el peor momento para enterarse.

Dos copias del HMAC en dos lenguajes es cómo se desincroniza del firmware, y la
divergencia se manifiesta como "el panel no conecta", que no dice nada.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from .cola import Pendiente

log = logging.getLogger("gtd.provisioner.broker")

# El script tarda: mosquitto_passwd, y en el camino manual también el reload y la
# prueba contra 8883. En lote esos dos van apagados, pero el margen queda igual.
TIMEOUT_S = 120


def argumentos(p: Pendiente, *, con_reload: bool = False) -> list[str]:
    """Los argumentos del script para esta operación.

    En lote nunca se recarga por equipo ni se publica la prueba: el reload va
    una sola vez al final, y la prueba ensuciaría el `first_connection_at` de
    toda la tanda con paneles que están en la caja.

    `manufacture` registra igual que `provision` — para el broker son la misma
    operación. Lo que las diferencia pasa DESPUÉS del script: la fabricación
    además deriva las credenciales del portal (ver `servicio.drenar`).
    """
    if p.op == "revoke":
        args = ["revoke", p.mac]
        if not con_reload:
            args.append("--no-reload")
        return args

    args = [p.mac]
    if not con_reload:
        args.append("--no-reload")
    args.append("--no-probe")
    return args


class Registrador:
    """Invoca el script real. El proceso tiene que correr como root."""

    def __init__(
        self, script: Path, salt: str = "", panel_password: str = "",
    ) -> None:
        self._script = script
        self._salt = salt
        self._panel_password = panel_password

    def _entorno(self) -> dict[str, str]:
        env = dict(os.environ)
        # El salt NUNCA por línea de comandos: quedaría en la lista de procesos
        # y en el historial. El script lo lee del entorno.
        if self._salt:
            env["SALT_MQTT"] = self._salt
        if self._panel_password:
            env["PANEL_PASSWORD"] = self._panel_password
        return env

    async def aplicar(self, p: Pendiente) -> tuple[str, str | None]:
        args = argumentos(p)
        proc = await asyncio.create_subprocess_exec(
            "bash", str(self._script), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._entorno(),
        )
        try:
            salida, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "error", f"el script no terminó en {TIMEOUT_S}s"

        if proc.returncode == 0:
            return "ok", None

        # Las últimas líneas son las que explican el fallo: el script muere con
        # `die`, que imprime el motivo. El salt no sale por acá — el script no
        # lo imprime nunca.
        lineas = salida.decode("utf-8", "replace").strip().splitlines()
        detalle = " | ".join(x.strip() for x in lineas[-3:] if x.strip())
        return "error", detalle or "el script falló sin decir por qué"

    async def recargar(self) -> tuple[str, str | None]:
        """Un solo REINICIO por tanda. No un reload, y eso costó una noche.

        El `systemctl reload` (SIGHUP) le pide a mosquitto que rearme su
        contexto TLS en caliente, y lo deja a medias: el listener 8883 sigue
        aceptando la conexión TCP pero **no completa el handshake**. Los paneles
        quedan reintentando cada 30 s con `MBEDTLS_ERR_SSL_FATAL_ALERT_MESSAGE`
        (-0x7280) justo después de validar el certificado, y en el log del
        broker se ve el síntoma exacto:

            New connection from 148.222.217.253 on port 8883    ← y nada más;
            New connection from 148.222.217.253 on port 8883      nunca un
            New connection from 148.222.217.253 on port 8883      "New client"

        Visto en producción el 2026-08-06: fabricar UN equipo dejó a toda la
        flota afuera durante horas, sin un solo error en el log. El reload no
        avisa que falló — por eso es peor que el corte.

        El restart corta las conexiones vivas, sí. Es un costo aceptado y
        decidido con el usuario: dura segundos, los paneles reconectan solos con
        su backoff, y el GtD también. La alternativa era una flota muda hasta
        que alguien se diera cuenta.

        Sigue siendo UNO POR TANDA: fabricar diez equipos reinicia una vez.
        """
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "restart", "mosquitto",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        salida, _ = await proc.communicate()
        if proc.returncode == 0:
            return "ok", None
        return "error", salida.decode("utf-8", "replace").strip()[:200]


class RegistradorFalso:
    """Doble para test: anota los argumentos y no toca nada."""

    def __init__(self, falla_en: set[str] | None = None) -> None:
        self.llamadas: list[list[str]] = []
        self.recargas = 0
        self._falla_en = falla_en or set()

    async def aplicar(self, p: Pendiente) -> tuple[str, str | None]:
        self.llamadas.append(argumentos(p))
        if p.mac in self._falla_en:
            return "error", "El salt NO reproduce el vector de verificación"
        return "ok", None

    async def recargar(self) -> tuple[str, str | None]:
        self.recargas += 1
        return "ok", None
