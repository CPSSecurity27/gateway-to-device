# 08 — Propuestas al firmware (2026-08-04)

> **De:** integración servidor (GtD + web) · **Para:** equipo firmware AlarmaV6
> Tres cosas que **solo el firmware puede arreglar**. El repo del firmware no
> se toca desde el lado servidor: esto es propuesta argumentada, no un parche.
> Referencias contra `AlarmaESP32V6_05-03-2026` tal como está hoy.

---

## F1 · `central` no vuelve en el `cfg_full` (ya levantado — se refuerza)

**Problema.** `mq_build_cfg_full()` (`components/main/task_mqtt.c:227`) emite
`redes`, `modulos`, `hora`, `tiempos`, `mante`, `alarma`, `cal`,
`red_avanzada`, `rf` e `id` — pero **no `central`** (alias / ubicación /
grupo). La web GENERA esa sección al publicar la cfg (la identidad no se
tipea), y sin el eco en el `cfg_full` **no hay forma de verificar que se
aplicó**: el espejo queda ciego justo en la sección que identifica el poste.

**Consecuencia si no se arregla.** Un panel con alias/grupo viejos es
indistinguible de uno actualizado. El día que un operador mire el portal local
del equipo y vea otro nombre que en la web, no va a haber manera de saber cuál
miente ni desde cuándo.

**Propuesta.** Sumar `"central":{"alias":…,"ubicacion":…,"grupo":…}` al
`cfg_full`, leyendo lo que el panel tiene efectivamente aplicado. Costo: un
bloque más de `snprintf` en el builder. Ojo con `MQTT_OUT_PAYLOAD` (el buffer
de salida): alias 31 + ubicación 63 + grupo 15 + sintaxis ≈ 140 bytes más.

**Prioridad:** alta (bloquea la verificación de la pantalla de configuración).

---

## F2 · La `cfg` retenida deja las passwords WiFi en el disco del broker

**Problema.** La cfg baja **retained** en `av/<id>/cfg` — correcto como diseño
(estado deseado; el panel la toma al conectar) — pero el payload lleva
`redes[].psw` **en claro**, y un mensaje retenido queda escrito en la
persistencia de Mosquitto **sin vencimiento**, hasta que otra cfg lo pise.

Hoy lo tapa la ACL (`pattern read av/%u/cfg`: solo ese panel y el usuario
`gateway`). Pero cuando el lado servidor cifre `gtd.panel_config` en reposo
(DT2), **el eslabón débil pasa a ser el disco del broker**, no Postgres: un
backup de `mosquitto.db`, o un acceso al filesystem del VPS, y las claves de
WiFi de todos los barrios están ahí.

**Propuesta** (cualquiera de las dos cierra el agujero; las dos son cambios de
firmware):

1. **La cfg deja de viajar retenida y el panel la PIDE al conectar** — un
   `cmd t:refresh` invertido: al conectar, el panel publica un `up` pidiendo
   config, el servidor responde por `av/<id>/cfg` SIN retain. El broker nunca
   persiste el payload. Costo: un mensaje más en el handshake de conexión, y
   el panel arranca con su NVS mientras tanto (ya lo hace).
2. **Passwords cifradas dentro del payload**, con una clave derivada del
   equipo (mismo esquema HMAC del provisioning): el broker guarda un blob. El
   panel descifra al aplicar. Costo: cripto en el firmware y gestión del
   material de clave.

La 1 es más simple y no agrega cripto; preferida desde el lado servidor.

**Prioridad:** media (hoy la ACL lo contiene). **Registrarla antes de dar DT2
por cerrado**: cifrar solo Postgres NO cierra DT2.

---

## F3 · Una alarma puede perderse si el panel reinicia antes del PUBACK

**Problema** (lo señaló el propio GtD en el doc 05 §6). El outbox de esp-mqtt
retransmite QoS 1 dentro de la sesión, pero si el panel **reinicia** (corte de
energía, brownout, watchdog) con una alarma encolada y sin PUBACK, esa alarma
no existe más: el outbox vive en RAM.

**Contexto.** El lado servidor ya cerró su mitad del mismo agujero: si
Postgres está caído cuando llega un `up`, el GtD lo persiste en un spool en
disco y lo reinyecta al recuperar (el PUBACK ya salió y el mensaje no existe
en ningún otro lado). Quedaría incoherente que la única copia perdible de una
alarma sea la del propio panel.

**Propuesta.** Persistir la cola de `up t:alarma` pendientes de PUBACK en NVS
(o el slot de EEPROM que ya usa la base RF): al reconectar tras un reboot,
republicar las que quedaron. El `eid` (`<boot_id>-<seq>`) ya hace el dedup del
lado servidor — republicar de más es gratis, perder de menos no.

**Prioridad:** media-alta (es el mensaje más importante del sistema; la
ventana es chica pero el costo de caer en ella es total).

---

*Contacto: los docs 07 (integración) y el contrato en el repo web
(`docs/contrato-gtd-postgres.md`) tienen el contexto completo.*
