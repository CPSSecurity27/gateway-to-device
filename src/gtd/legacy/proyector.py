"""El bucle de bajada: Postgres → lo que la app vieja lee.

Dos trabajos con ritmos distintos:

- **Los eventos**, por `LISTEN app_event`: una emergencia tiene que aparecer en
  el teléfono del vecino ya, no en el próximo barrido.
- **El catálogo `ClientesID`**, por barrido: un alta o una suspensión que tarda
  un minuto en verse no le arruina el día a nadie, y escuchar cada cambio de
  `app_user`/`home`/`home_member` sería tres triggers más para algo temporal.

Sobre los dos va un **barrido de reconciliación**. `LISTEN/NOTIFY` no tiene
memoria: un aviso emitido mientras el proceso estaba caído no vuelve nunca. Sin
el barrido, un reinicio en el momento equivocado deja a la app mostrando
'Conectada' con una emergencia abierta.
"""

from __future__ import annotations

import asyncio
import logging

from .aviso import Fcm
from .espejo import Espejo
from .puerta import Puerta
from .rtdb import Rtdb

log = logging.getLogger("gtd.legacy.proyector")


async def proyectar_evento(
    device_id: int, puerta: Puerta, espejo: Espejo, fcm: Fcm | None,
    avisados: set[int],
) -> None:
    """Refresca un equipo y, si hay emergencia nueva, avisa al barrio."""
    s = await puerta.snapshot(device_id)
    if s is None:
        return                      # el equipo no es de la puerta vieja

    await espejo.proyectar(s)

    # El push va UNA vez por evento. `avisados` es en memoria a propósito: si el
    # proceso se reinicia con una emergencia abierta, mandar el aviso de nuevo
    # es mucho menos grave que no mandarlo — que es exactamente el modo de falla
    # que este sistema no se puede permitir.
    if fcm is not None and s.hay_emergencia and s.event_id not in avisados:
        await fcm.avisar(s.marcador, s.usuario or "", s.modoalarma or "")
        avisados.add(s.event_id)


async def proyectar_clientes(puerta: Puerta, rtdb: Rtdb) -> int:
    """`ClientesID/<DNI>` desde Postgres. Devuelve cuántos escribió.

    Es lo que corta la deriva: sin esto, un vecino cargado desde el panel web no
    puede entrar a la app vieja (su login es "existe este nodo") y una
    suspensión hecha en el panel nunca le llega.

    Se escribe con PATCH y no con PUT: el nodo tiene campos que NO manejamos
    —`Inicio`, que lo escribe la propia app al loguearse, y `ControlRF`, que
    quedó de la carga original— y un PUT los borraría.
    """
    filas = await puerta.clientes()
    for c in filas:
        familia = {"nuser": c["cupo"]}
        for i, dni_fam in enumerate(c["familia"], start=1):
            familia[f"usuario{i}"] = dni_fam

        await rtdb.patch(f"ClientesID/{c['dni']}", {
            "Usuario": c["usuario"] or "",
            "Telefono": c["telefono"] or "",
            "Direccion": c["direccion"] or "",
            "Marcador": c["marcador"],
            "Suspension": c["suspension"],
        })
        # `familia` sí va con PUT: si a alguien lo dieron de baja, su clave
        # `usuarioN` tiene que DESAPARECER, y un merge la dejaría para siempre.
        await rtdb.put(f"ClientesID/{c['dni']}/familia", familia)

    return len(filas)


async def barrer(
    puerta: Puerta, espejo: Espejo, fcm: Fcm | None, avisados: set[int],
) -> None:
    for device_id, _marcador in await puerta.devices():
        await proyectar_evento(device_id, puerta, espejo, fcm, avisados)


async def bucle(
    puerta: Puerta,
    espejo: Espejo,
    rtdb: Rtdb,
    fcm: Fcm | None,
    barrido_s: float,
    clientes_s: float,
) -> None:
    avisados: set[int] = set()

    # Arranque: reconciliar antes de escuchar. Lo primero que hay que saber es
    # si mientras estuvimos caídos quedó una emergencia abierta.
    try:
        await barrer(puerta, espejo, fcm, avisados)
        log.info("[proyector] barrido inicial hecho")
    except Exception as e:                                    # noqa: BLE001
        log.error("[proyector] el barrido inicial falló: %s", e)

    ultimo_clientes = 0.0
    loop = asyncio.get_running_loop()

    while True:
        try:
            device_id = await puerta.proximo_evento(barrido_s)
            if device_id is not None:
                await proyectar_evento(device_id, puerta, espejo, fcm, avisados)
            else:
                await barrer(puerta, espejo, fcm, avisados)

            ahora = loop.time()
            if ahora - ultimo_clientes >= clientes_s:
                n = await proyectar_clientes(puerta, rtdb)
                ultimo_clientes = ahora
                log.info("[proyector] %s clientes proyectados", n)

        except asyncio.CancelledError:
            raise
        except Exception as e:                                # noqa: BLE001
            # Firebase caído, token vencido, la base que se fue: se loguea y se
            # reintenta en el próximo ciclo. Este bucle no se puede morir.
            log.error("[proyector] ciclo fallido: %s", e)
            await asyncio.sleep(5.0)
