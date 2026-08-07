"""Push FCM al barrio — lo único que hoy le avisa a un vecino que sonó la alarma.

La app se suscribe al tópico FCM que es **el marcador crudo**
(`_subscribeToMarkerTopic`: `marcador.trim().toUpperCase()`), o sea
`CENTRALVECINAL05`. No hay tokens guardados en ningún lado: es por tópico.

Se usa la API **HTTP v1** (`/v1/projects/<id>/messages:send`) y no la legacy
`/fcm/send`, que Google dio de baja. El formato del mensaje es el de v1: `data`
tiene que ser todo strings, y `notification` va adentro de `message`.

El texto reproduce el del broker-bridge —"Activacion por X, por motivo Y"— para
que la notificación siga leyéndose igual que siempre. Con el canal Android
`alarmas`, que es el que la app tiene creado con importancia alta; un canal que
no existe hace que la notificación llegue sin sonido.
"""

from __future__ import annotations

import logging

import httpx

from .google import CuentaDeServicio

log = logging.getLogger("gtd.legacy.aviso")

SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

# El canal que la app crea en `_initLocalNotifications`. Tiene que coincidir o
# la notificación llega muda.
CANAL_ANDROID = "alarmas"


class Fcm:
    def __init__(
        self,
        cuenta: CuentaDeServicio,
        cliente: httpx.AsyncClient,
        project_id: str,
    ) -> None:
        self._cuenta = cuenta
        self._http = cliente
        self._url = (
            f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
        )

    async def avisar(
        self, topico: str, usuario: str, motivo: str,
    ) -> str | None:
        """Devuelve el id del mensaje, o None si falló. Nunca levanta.

        Que el push falle NO puede tumbar la proyección: la alarma ya sonó y el
        estado ya se escribió. Un aviso perdido es malo; perder también la
        pantalla de la app por eso sería peor.
        """
        cuerpo = f"Activacion por {usuario or 'un vecino'}, por motivo {motivo or 'alarma'}"
        mensaje = {
            "message": {
                "topic": topico,
                "notification": {"title": "Alarma Vecinal", "body": cuerpo},
                # En v1 TODO `data` es string. Las claves son las que la app
                # espera en `msg.data`.
                "data": {
                    "user": usuario or "",
                    "tipo": motivo or "",
                    "screen": "alarm_detail",
                },
                "android": {
                    "priority": "high",
                    "notification": {
                        "channel_id": CANAL_ANDROID,
                        "sound": "default",
                    },
                },
                "apns": {
                    "headers": {"apns-priority": "10"},
                    "payload": {"aps": {"sound": "default"}},
                },
            },
        }

        try:
            r = await self._http.post(
                self._url, json=mensaje,
                headers=await self._cuenta.headers(self._http), timeout=20.0,
            )
            r.raise_for_status()
            nombre = r.json().get("name")
            log.info("[fcm] enviado topico=%s id=%s", topico, nombre)
            return nombre
        except Exception as e:                                # noqa: BLE001
            detalle = ""
            if isinstance(e, httpx.HTTPStatusError):
                detalle = f" — {e.response.text[:200]}"
            log.error("[fcm] falló el aviso a %s: %s%s", topico, e, detalle)
            return None
