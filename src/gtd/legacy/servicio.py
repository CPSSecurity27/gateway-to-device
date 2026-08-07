"""El bucle: escuchar `cliente/servidor` y traducir cada mensaje.

Un mensaje NUNCA puede tumbar el proceso. Este servicio es lo único que le queda
a los vecinos que no actualizaron la app: si muere por un JSON raro, se quedan
sin poder activar la alarma y nadie se entera hasta que alguien la necesita. Por
eso cada mensaje va adentro de su propio try, y lo peor que puede pasar es una
línea de error en el journal.
"""

from __future__ import annotations

import logging

from .freno import Freno
from .mensaje import MensajeInvalido, parse
from .puerta import Puerta

log = logging.getLogger("gtd.legacy")


async def procesar(payload: bytes | str, puerta: Puerta, freno: Freno) -> str:
    """Traduce un mensaje. Devuelve el resultado, para el log y para los tests."""
    try:
        act = parse(payload)
    except MensajeInvalido as e:
        log.warning("[legacy] mensaje descartado: %s", e)
        return "malformado"

    motivo = freno.permite(act.dni, es_desactivacion=act.es_desactivacion)
    if motivo is not None:
        log.warning("[legacy] frenado dni=%s modo=%s (%s)", act.dni, act.modo, motivo)
        return motivo

    try:
        res = await puerta.activar(act)
    except Exception as e:                                    # noqa: BLE001
        # La base caída, un timeout, un permiso mal puesto. Se loguea y se
        # sigue: el próximo mensaje puede andar.
        log.error("[legacy] error al encolar dni=%s modo=%s: %s", act.dni, act.modo, e)
        return "error"

    if res.acepto:
        log.info(
            "[legacy] %s dni=%s modo=%s cid=%s%s",
            "DESACTIVA" if act.es_desactivacion else "ACTIVA",
            act.dni, act.modo, res.cid,
            "" if act.lat is None else f" gps={act.lat:.5f},{act.lng:.5f}",
        )
    else:
        # Un rechazo no es un bug: un DNI que no existe o un hogar sin alarma
        # preferida son estados normales. Se loguea con el motivo que dio la
        # base, con su nombre exacto, para poder buscarlo en la migración.
        log.warning(
            "[legacy] rechazada dni=%s modo=%s: %s", act.dni, act.modo, res.resultado,
        )
    return res.resultado


async def bucle(cliente, topico: str, puerta: Puerta, freno: Freno) -> None:
    """Consume el tópico hasta que se corte la conexión."""
    await cliente.subscribe(topico, qos=1)
    log.info("[legacy] suscripto a %s", topico)

    async for msg in cliente.messages:
        try:
            await procesar(msg.payload, puerta, freno)
        except Exception as e:                                # noqa: BLE001
            # Red de seguridad: procesar() ya atrapa lo suyo, pero un mensaje
            # no puede terminar con el servicio ni por algo que no previmos.
            log.exception("[legacy] error inesperado procesando un mensaje: %s", e)
