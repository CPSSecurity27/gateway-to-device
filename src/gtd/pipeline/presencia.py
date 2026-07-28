"""Presencia de los paneles: quién está vivo, quién reconectó, quién se calló.

Por qué existe. La presencia no se puede deducir solo del canal `status`:

  - En un **takeover** de sesión (el panel reconecta desde otra IP antes de que el
    broker note que la anterior murió) mosquitto expulsa la sesión vieja y **no**
    publica el LWT. El panel republica `online` y, para el GtD, nunca se fue.
    Medido contra una placa real sobre Starlink: 31 cortes del broker, apenas unos
    pocos visibles en el log del GtD.
  - Un panel que enmudece sin que venza el keepalive queda `online` para siempre.

Así que se mide lo único que no miente: **cuándo habló por última vez**.

Todo el tiempo se maneja con `monotonic()`, no con la hora del reloj: los
intervalos tienen que sobrevivir a un salto de NTP o a un cambio de zona horaria.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

# Estados del contrato que implican silencio esperado (el panel avisó que se iba).
DORMIDO = "durmiendo"


class EventoStatus(str, Enum):
    """Qué significa un `status` recién llegado, dado lo que ya sabíamos."""
    PRIMERA_VEZ = "primera_vez"   # no lo habíamos visto (o el GtD acaba de reconectar)
    CAMBIO = "cambio"             # transición real de estado
    RECONEXION = "reconexion"     # mismo estado, pero venía callado: volvió a conectarse
    REPETIDO = "repetido"         # retained reentregado, sin novedad


@dataclass
class Panel:
    estado: str | None = None
    ultimo_msg: float = field(default_factory=time.monotonic)
    reconexiones: int = 0
    despierta: int | None = None      # unix UTC hasta cuándo duerme (si duerme)
    sin_senal: bool = False           # el watchdog ya lo dio por caído


@dataclass
class Actividad:
    silencio: float                   # segundos desde el mensaje anterior
    volvio: bool                      # estaba dado por caído y habló


_paneles: dict[str, Panel] = {}


def reiniciar() -> None:
    """Olvida todo. Se llama cuando el GtD reconecta al broker.

    Es imprescindible: al resuscribirse, el broker reentrega los `status`
    retenidos de TODA la flota de una. Sin este reset, cada uno de esos mensajes
    parecería una reconexión del panel, cuando el que reconectó fue el GtD.
    """
    _paneles.clear()


def actividad(mac: str) -> Actividad:
    """Registra que el panel habló (cualquier canal). Devuelve el silencio previo."""
    ahora = time.monotonic()
    p = _paneles.get(mac)
    if p is None:
        _paneles[mac] = Panel(ultimo_msg=ahora)
        return Actividad(silencio=0.0, volvio=False)

    silencio = ahora - p.ultimo_msg
    p.ultimo_msg = ahora
    volvio = p.sin_senal
    p.sin_senal = False
    return Actividad(silencio=silencio, volvio=volvio)


def ver_status(mac: str, estado: str, *, silencio: float, gap: float,
               despierta: int | None = None) -> tuple[EventoStatus, str]:
    """Clasifica un `status`. Devuelve (evento, estado_anterior).

    `gap` = silencio mínimo para considerarlo reconexión. Las dos señales juntas
    —mismo estado Y silencio previo— hacen que esto siga funcionando aunque el
    firmware empiece a emitir `status` periódicamente: un anuncio periódico llega
    con la cadencia intacta, una reconexión llega después de un hueco.
    """
    p = _paneles.setdefault(mac, Panel())
    previo, p.estado = p.estado, estado
    p.despierta = despierta if estado == DORMIDO else None
    anterior = previo if previo is not None else "?"

    if previo is None:
        return EventoStatus.PRIMERA_VEZ, anterior
    if estado != previo:
        return EventoStatus.CAMBIO, anterior
    if estado != DORMIDO and silencio >= gap:
        p.reconexiones += 1
        return EventoStatus.RECONEXION, anterior
    return EventoStatus.REPETIDO, anterior


def reconexiones(mac: str) -> int:
    p = _paneles.get(mac)
    return p.reconexiones if p else 0


def sin_senal(timeout: float) -> list[tuple[str, float]]:
    """Paneles callados hace más de `timeout`. Devuelve [(mac, segundos)].

    Marca los que devuelve, para no repetir el aviso en cada pasada.

    Un panel `durmiendo` NO cuenta como caído: avisó que se iba y hasta cuándo.
    Pero si pasó su hora de despertar con margen y sigue mudo, ahí sí es una
    falla — un panel que se durmió y no volvió es invisible de otro modo.
    """
    ahora = time.monotonic()
    ahora_unix = time.time()
    vencidos: list[tuple[str, float]] = []

    for mac, p in _paneles.items():
        if p.sin_senal:
            continue
        callado = ahora - p.ultimo_msg
        if callado < timeout:
            continue
        if p.estado == DORMIDO:
            # Silencio esperado, salvo que ya tendría que haber vuelto.
            if p.despierta is None or ahora_unix < p.despierta + timeout:
                continue
        p.sin_senal = True
        vencidos.append((mac, callado))

    return vencidos
