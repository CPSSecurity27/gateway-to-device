"""El bucle del provisioner: drenar la cola y volver a mirar.

La lógica vive acá y no en `__main__.py` para que los tests la puedan importar
sin ejecutar el entrypoint.
"""

from __future__ import annotations

import asyncio
import logging

from .cola import Cola, Pendiente

log = logging.getLogger("gtd.provisioner")

# Cada cuánto se barre aunque no haya llegado ningún NOTIFY. Un NOTIFY emitido
# mientras esto estaba caído no vuelve nunca — la misma lección que el barrido
# de pendientes del GtD (P0-1). El NOTIFY acelera; el barrido garantiza.
#
# Ojo con subirlo: el alta de fábrica es SINCRÓNICA y la web espera 30 s. Sin el
# NOTIFY —conexión de escucha caída, por ejemplo— cualquier barrido más largo
# que ese timeout hace que TODA fabricación se venza y el equipo se borre.
BARRIDO_S = 20


async def drenar(cola: Cola, registrador, fabricador=None) -> int:
    """Procesa todos los pendientes y recarga UNA vez. Devuelve cuántos hizo.

    Va en DOS FASES —trabajar, recargar, recién ahí confirmar— y no de a un
    equipo por vez. La razón es la fabricación: la web encola un `manufacture` y
    se queda esperando esa confirmación para responder el alta. Si se confirmara
    antes del reload, la web diría "equipo listo" con Mosquitto todavía sin leer
    la credencial — exactamente la mentira que el alta atómica vino a evitar.

    Un fallo se confirma como `error` y se sigue con el resto: una MAC con
    problemas no puede dejar sin credencial a los otros 199 de la tanda.
    """
    pendientes = await cola.pendientes()
    if not pendientes:
        return 0

    log.info("procesando %d pendiente(s)", len(pendientes))

    # ── Fase 1: el trabajo, sin confirmar nada todavía ──────────────────
    hechos: list[tuple[Pendiente, str, str | None, str | None, str | None]] = []
    hubo_fabricacion = False

    for p in pendientes:
        res, det = await registrador.aplicar(p)
        admin_enc = cps_enc = None

        if p.es_fabricacion:
            hubo_fabricacion = True
            if res == "ok":
                if fabricador is None:
                    res, det = "error", (
                        "el provisioner no tiene los salts del portal: "
                        "no puede fabricar"
                    )
                else:
                    try:
                        admin_enc, cps_enc = fabricador.credenciales(p.mac)
                    except Exception as e:                   # noqa: BLE001
                        # La credencial MQTT ya quedó escrita pero el equipo no
                        # se va a poder abrir nunca. Es un fallo del alta: la web
                        # borra el equipo y el barrido de huérfanos limpia lo que
                        # quedó en el broker.
                        res, det = "error", f"derivación del portal: {e}"

        hechos.append((p, res, det, admin_enc, cps_enc))

    # ── Fase 2: un solo reload ──────────────────────────────────────────
    # Se recarga aunque alguno haya fallado: los que SÍ salieron tienen que
    # quedar activos, y dejarlos sin reload sería peor que el fallo original.
    res_reload, det_reload = await registrador.recargar()
    if res_reload != "ok":
        log.error("el reload de mosquitto falló: %s", det_reload)
    elif hubo_fabricacion:
        log.info("mosquitto recargado: las credenciales fabricadas ya valen")

    # ── Fase 3: confirmar ───────────────────────────────────────────────
    for p, res, det, admin_enc, cps_enc in hechos:
        if p.es_fabricacion:
            # Sin reload la credencial existe en el archivo pero el broker no la
            # conoce: el equipo NO puede conectarse. Para una op atómica eso es
            # un fallo, no un detalle. Las `provision` sueltas se confirman igual
            # —tienen botón de reintento y nadie está esperándolas—, y cambiar
            # eso acá sería cambiar un comportamiento que no vinimos a tocar.
            if res == "ok" and res_reload != "ok":
                res = "error"
                det = f"la credencial no quedó activa: {det_reload}"
                admin_enc = cps_enc = None

            if res == "ok":
                log.info("manufacture %s ok", p.mac)
            else:
                log.error("manufacture %s falló: %s", p.mac, det)
            await cola.confirmar_manufactura(p.id, res, admin_enc, cps_enc, det)
        else:
            if res == "ok":
                log.info("%s %s ok", p.op, p.mac)
            else:
                log.error("%s %s falló: %s", p.op, p.mac, det)
            await cola.confirmar(p.id, res, det)

    return len(pendientes)


async def bucle(
    cola: Cola, registrador, intervalo: float = BARRIDO_S, fabricador=None,
) -> None:
    """Corre para siempre. Un error en un barrido no puede matar el servicio."""
    log.info("Provisioner arrancando — aviso por NOTIFY, barrido cada %ss", intervalo)
    while True:
        try:
            await drenar(cola, registrador, fabricador)
        except Exception as e:                       # noqa: BLE001
            # La cola sigue viva: el próximo barrido lo reintenta. Morir acá
            # dejaría equipos sin credencial y nadie mirando.
            log.error("barrido falló: %s", e)

        # Despierta al toque si la base avisa. Sin esto el alta de fábrica
        # esperaba hasta un barrido entero y la web la daba por vencida.
        await cola.esperar_trabajo(intervalo)
