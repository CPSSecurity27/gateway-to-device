"""La BAJADA: proyectar Postgres a lo que la app vieja sabe leer.

Postgres es la única verdad. Firebase pasa a ser una PROYECCIÓN de solo
lectura que existe nada más que para que la app vieja siga viendo lo que espera.

Tres nodos, relevados del `main.dart` de la app instalada:

    <Marcador>/DatosCentral/Estado                       'Conectada'|'Activada'|otro
    <Marcador>/Instrucciones/InstruccionesActivacion     la tarjeta de quién activó
    <Marcador>/Instrucciones/Historial/<ts>              la pestaña Historial

**`Estado` no es decorativo.** La app hace `if (estado == 'Activada' &&
activation != null)` para decidir si muestra la tarjeta. Si ese string no dice
exactamente eso, los datos se escriben bien y el vecino no ve nada.

La clave del historial tiene un formato propio, `DD-MM-YYYYTHH:MM:SSZ`, que NO
es ISO-8601 (el día va primero). La app lo parsea con un regex explícito y, si
no matchea, cae en `DateTime.now()` — o sea que un formato equivocado no falla:
muestra todos los eventos como si fueran de recién.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .rtdb import Rtdb

log = logging.getLogger("gtd.legacy.espejo")

# El vocabulario cerrado de la app. Cualquier otro valor se muestra en gris.
ACTIVADA = "Activada"


@dataclass(frozen=True)
class Snapshot:
    """Lo que devuelve gtd.legacy_snapshot para un equipo."""
    marcador: str
    estado: str
    event_id: int | None
    usuario: str | None
    telefono: str | None
    direccion: str | None
    modoalarma: str | None
    gps_lat: float | None
    gps_lng: float | None
    creado: datetime | None

    @property
    def hay_emergencia(self) -> bool:
        return self.estado == ACTIVADA and self.event_id is not None


def clave_historial(cuando: datetime) -> str:
    """`DD-MM-YYYYTHH:MM:SSZ` en UTC. Ojo: NO es ISO-8601, el día va primero.

    Es el formato que la app parsea con su regex; cambiarlo hace que todos los
    eventos del historial se muestren con la hora actual.
    """
    return cuando.astimezone(timezone.utc).strftime("%d-%m-%YT%H:%M:%SZ")


def tarjeta(s: Snapshot) -> dict:
    """`InstruccionesActivacion`. Claves en minúscula: así las lee la app
    (`val['usuario']`, `val['modoalarma']`, `gps['latitud']`)."""
    doc: dict = {
        "usuario": s.usuario or "",
        "telefono": s.telefono or "",
        "direccion": s.direccion or "",
        "modoalarma": s.modoalarma or "",
    }
    if s.gps_lat is not None and s.gps_lng is not None:
        # Como STRING y con 6 decimales, igual que lo manda la app: su
        # `_toDoubleClean` parsea texto, y así el formato es uno solo.
        doc["GPS"] = {
            "latitud": f"{s.gps_lat:.6f}",
            "longitud": f"{s.gps_lng:.6f}",
        }
    return doc


def entrada_historial(s: Snapshot) -> dict:
    """El historial guarda MENOS que la tarjeta: la app solo lee estas tres."""
    return {
        "usuario": s.usuario or "",
        "direccion": s.direccion or "",
        "modoalarma": s.modoalarma or "",
    }


class Espejo:
    """Proyecta un equipo. Recuerda qué evento ya historió para no duplicar."""

    def __init__(self, rtdb: Rtdb) -> None:
        self._rtdb = rtdb
        # event_id ya escrito en el historial, por marcador. En memoria: si el
        # proceso se reinicia, lo peor que pasa es una entrada repetida en el
        # historial (misma clave = mismo segundo ⇒ la RTDB la pisa, no duplica).
        self._historiado: dict[str, int] = {}
        # Lo ÚLTIMO que escribimos, por marcador. Existe para no reescribir lo
        # mismo en cada barrido: sin esto son dos PUT cada 30 s por equipo
        # —unos 5.800 escrituras diarias— que no cambian nada, gastan cuota y
        # tapan en el journal los eventos que sí importan.
        #
        # Que el barrido igual llame a proyectar() no es al pedo: es el que
        # detecta el cambio que el NOTIFY se pudo haber perdido.
        self._ultimo_estado: dict[str, str] = {}
        self._ultima_tarjeta: dict[str, dict] = {}

    async def proyectar(self, s: Snapshot) -> None:
        m = s.marcador

        # 1. El estado. Es lo que gobierna qué muestra la app.
        if self._ultimo_estado.get(m) != s.estado:
            await self._rtdb.put(f"{m}/DatosCentral/Estado", s.estado)
            self._ultimo_estado[m] = s.estado
            log.info("[espejo] %s -> %s", m, s.estado)

        if not s.hay_emergencia:
            # Sin emergencia no se BORRA la tarjeta: la app solo la muestra
            # cuando Estado == 'Activada', así que dejarla es inofensivo, y
            # borrarla haría parpadear la pantalla de quien la está mirando
            # justo cuando se cierra el evento.
            self._ultima_tarjeta.pop(m, None)
            return

        # 2. La tarjeta de quién activó.
        t = tarjeta(s)
        if self._ultima_tarjeta.get(m) != t:
            await self._rtdb.patch(
                f"{m}/Instrucciones/InstruccionesActivacion", t,
            )
            self._ultima_tarjeta[m] = t
            log.info(
                "[espejo] %s ACTIVADA por %s (%s) evento=%s",
                m, s.usuario, s.modoalarma, s.event_id,
            )

        # 3. El historial, UNA sola vez por evento.
        if self._historiado.get(m) != s.event_id:
            cuando = s.creado or datetime.now(timezone.utc)
            await self._rtdb.put(
                f"{m}/Instrucciones/Historial/{clave_historial(cuando)}",
                entrada_historial(s),
            )
            self._historiado[m] = s.event_id  # type: ignore[assignment]
