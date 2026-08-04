"""El bucle del provisioner: drenar la cola y volver a mirar.

La lógica vive acá y no en `__main__.py` para que los tests la puedan importar
sin ejecutar el entrypoint.
"""

from __future__ import annotations

import asyncio
import logging

from .cola import Cola

log = logging.getLogger("gtd.provisioner")

# Cada cuánto se barre aunque no haya llegado ningún NOTIFY. Un NOTIFY emitido
# mientras esto estaba caído no vuelve nunca — la misma lección que el barrido
# de pendientes del GtD (P0-1). El NOTIFY acelera; el barrido garantiza.
BARRIDO_S = 60


async def drenar(cola: Cola, registrador) -> int:
    """Procesa todos los pendientes y recarga UNA vez. Devuelve cuántos hizo.

    Un fallo se confirma como `error` y se sigue con el resto: una MAC con
    problemas no puede dejar sin credencial a los otros 199 de la tanda.
    """
    pendientes = await cola.pendientes()
    if not pendientes:
        return 0

    log.info("procesando %d pendiente(s)", len(pendientes))
    for p in pendientes:
        res, det = await registrador.aplicar(p)
        if res == "ok":
            log.info("%s %s ok", p.op, p.mac)
        else:
            log.error("%s %s falló: %s", p.op, p.mac, det)
        await cola.confirmar(p.id, res, det)

    # Se recarga aunque alguno haya fallado: los que SÍ salieron tienen que
    # quedar activos, y dejarlos sin reload sería peor que el fallo original.
    res, det = await registrador.recargar()
    if res != "ok":
        log.error("el reload de mosquitto falló: %s", det)

    return len(pendientes)


async def bucle(cola: Cola, registrador, intervalo: float = BARRIDO_S) -> None:
    """Corre para siempre. Un error en un barrido no puede matar el servicio."""
    log.info("Provisioner arrancando — barrido cada %ss", intervalo)
    while True:
        try:
            await drenar(cola, registrador)
        except Exception as e:                       # noqa: BLE001
            # La cola sigue viva: el próximo barrido lo reintenta. Morir acá
            # dejaría equipos sin credencial y nadie mirando.
            log.error("barrido falló: %s", e)
        await asyncio.sleep(intervalo)
