"""Freno anti-abuso del adaptador.

Por qué existe: el listener 1883 es ANÓNIMO y está abierto
(`deploy/legacy-1883.acl`: `topic readwrite #`), y la app vieja no autentica a
nadie —su login es "existe este DNI"—. O sea que cualquiera con internet puede
publicar activaciones a nombre de cualquier DNI, tantas como quiera.

Eso ya era verdad con el broker-bridge. Lo que cambia es que ahora esas
activaciones quedan firmadas con el nombre y el teléfono de un vecino real, y
hacen sonar una sirena. Un freno no arregla la falta de autenticación —eso no
tiene arreglo sin tocar la app—, pero convierte "puedo hacer sonar el barrio
toda la noche" en "puedo hacerlo sonar una vez y quedar en la auditoría".

Dos frenos, porque tapan agujeros distintos:

- **Por DNI**: el mismo vecino no dispara dos veces seguidas. Cubre también el
  reenvío de QoS 1, que si no encolaría el comando dos veces.
- **Global**: alguien barriendo DNIs al azar pasa el freno por DNI sin
  despeinarse, porque cada mensaje trae uno distinto.

El cps999 (desactivar) NO se frena NUNCA, por ninguno de los dos. Apagar una
alarma tiene que funcionar siempre: el costo de un desarme de más es ruido, y el
de un desarme bloqueado es una sirena sonando toda la noche.
"""

from __future__ import annotations

import time
from collections import deque


class Freno:
    def __init__(
        self,
        por_dni_s: float = 3.0,
        global_por_min: int = 30,
        ahora=time.monotonic,
    ) -> None:
        self._por_dni_s = por_dni_s
        self._global_por_min = global_por_min
        self._ahora = ahora
        self._ultimo: dict[str, float] = {}
        self._ventana: deque[float] = deque()

    def permite(self, dni: str, *, es_desactivacion: bool = False) -> str | None:
        """None = pasa. Un string = el motivo por el que se frenó."""
        if es_desactivacion:
            return None

        t = self._ahora()

        self._purgar(t)
        if self._global_por_min > 0 and len(self._ventana) >= self._global_por_min:
            return "freno_global"

        anterior = self._ultimo.get(dni)
        if anterior is not None and (t - anterior) < self._por_dni_s:
            return "freno_por_dni"

        self._ultimo[dni] = t
        self._ventana.append(t)
        return None

    def _purgar(self, t: float) -> None:
        limite = t - 60.0
        while self._ventana and self._ventana[0] < limite:
            self._ventana.popleft()
        # El diccionario por DNI también se poda, o crece para siempre con un
        # barrido de DNIs al azar. Solo importa lo reciente.
        if len(self._ultimo) > 4096:
            self._ultimo = {
                d: v for d, v in self._ultimo.items() if (t - v) < self._por_dni_s
            }
