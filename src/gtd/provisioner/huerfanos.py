"""Barrido de credenciales huérfanas en el broker.

El alta de la web es atómica por COMPENSACIÓN: crea el equipo, espera al
provisioner y lo BORRA si algo falló. Si el fallo ocurrió después de que
`provision-panel.sh` escribiera en `gtd.passwd`, queda un usuario registrado sin
ningún equipo que lo use. Nadie lo va a notar: no rompe nada, no aparece en
ninguna pantalla, y sigue siendo una credencial válida contra el broker.

Por eso se barre al arrancar, igual que la cola. Un `NOTIFY` perdido y una
credencial huérfana son el mismo tipo de bug: estado que quedó desincronizado
mientras nadie miraba.

## El rail de seguridad

`gtd.passwd` también tiene el usuario del propio GtD (`gateway`) y cualquier
cuenta de servicio que se haya creado a mano. Un barrido ingenuo las borraría
todas y dejaría el sistema entero sin puente. Así que **solo se consideran los
usuarios con forma de equipo** (`AV-` + 12 hex): lo que no matchea ese patrón no
se toca ni se cuenta.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .cola import Cola, Pendiente

log = logging.getLogger("gtd.provisioner.huerfanos")

PASSWD_FILE = Path("/etc/mosquitto/gtd.passwd")

# El usuario MQTT ES el serial: 'AV-' + la MAC STA. Cualquier otra cosa en el
# archivo es una cuenta de servicio y no es asunto de este barrido.
RE_EQUIPO = re.compile(r"^AV-[0-9A-F]{12}$")


def usuarios_de_equipo(texto: str) -> set[str]:
    """Los usuarios con forma de equipo que hay en un `gtd.passwd`.

    El formato es `usuario:hash` por línea. Se ignoran vacías y comentarios.
    """
    encontrados = set()
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or ":" not in linea:
            continue
        usuario = linea.split(":", 1)[0]
        if RE_EQUIPO.match(usuario):
            encontrados.add(usuario)
    return encontrados


async def barrer(
    cola: Cola, registrador, passwd: Path = PASSWD_FILE, *, aplicar: bool = True,
) -> list[str]:
    """Revoca los usuarios de equipo que ya no tienen fila en `device`.

    Con `aplicar=False` solo informa — sirve para mirar qué haría antes de
    dejarlo suelto sobre un broker de producción.
    """
    if not passwd.is_file():
        log.warning("no existe %s: no hay nada que barrer", passwd)
        return []

    try:
        registrados = usuarios_de_equipo(passwd.read_text("utf-8", "replace"))
    except OSError as e:
        log.error("no se pudo leer %s: %s", passwd, e)
        return []

    vivos = await cola.seriales_vivos()
    huerfanos = sorted(registrados - vivos)

    if not huerfanos:
        log.info("barrido de huérfanos: %d equipo(s) registrado(s), ninguno sobra",
                 len(registrados))
        return []

    log.warning("barrido de huérfanos: %d credencial(es) sin equipo: %s",
                len(huerfanos), ", ".join(huerfanos))
    if not aplicar:
        return huerfanos

    revocados = []
    for serial in huerfanos:
        mac = serial[3:]                       # 'AV-' + 12 hex
        res, det = await registrador.aplicar(Pendiente(0, mac, "revoke"))
        if res == "ok":
            revocados.append(serial)
        else:
            # No aborta el barrido: una revocación que falla no puede dejar las
            # otras sin limpiar.
            log.error("no se pudo revocar %s: %s", serial, det)

    if revocados:
        res, det = await registrador.recargar()
        if res != "ok":
            log.error("el reload tras el barrido falló: %s", det)

    return revocados
