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

## F4 · Aplicar una `cfg` no refresca el espejo de forma confiable

**Problema.** `mq_apply_cfg()` (`components/main/task_mqtt.c:424`) **no** llama a
`system_state_cfg_full_touch()`. Que el espejo se actualice o no depende de qué
sección tocó el patch, porque el touch está escondido adentro de algunos
setters y no de otros:

| Sección | Setter | ¿Refresca el espejo? |
|---|---|---|
| `red_avanzada` | `app_roam_set` (`task_wifi.c:31`) | **sí** |
| `alarma.autooff` | `app_autooff_set_mode` (`task_alarma.c:130`) | **sí** |
| `mante` | `app_mante_set` | **sí** |
| `tiempos` | `eeprom_nvs_mqtt_set_tele_s` **directo** (línea 440) | **no** |
| `redes` | `eeprom_nvs_save_credentials` directo | **no** |
| `hora`, `central` | setters directos | **no** |

Y se nota que la intención era refrescar siempre: el mismo cambio hecho desde el
portal local pasa por `app_tele_period_set()` (línea 215), que **sí** hace el
touch. Los dos caminos escriben lo mismo y se comportan distinto.

**Consecuencia si no se arregla.** Del lado servidor no podemos confiar en el
`cfg_full` después de una cfg, así que **encadenamos un `cmd t:refresh` a cada
publicación**: un comando extra por cada cambio de configuración de cada panel de
la flota. Y como el `cfg_full` es el único lugar donde se ve qué quedó después de
los clamps silenciosos, sin ese refresh la pantalla no puede decir la verdad.

**Propuesta.** Una línea: `system_state_cfg_full_touch()` al final de
`mq_apply_cfg()`, sin condición, después de `eeprom_nvs_mqtt_set_cfg_v()`. Los
setters que ya lo llaman quedan idempotentes (es un flag, no un envío). Con eso
el `refresh` encadenado se puede sacar del servidor.

**Prioridad:** alta (nos ahorra un comando por cada cambio de config, y es una línea).

---

## F5 · Una `cfg` malformada es silencio total

**Problema.** En `mq_handle_cfg()` (`task_mqtt.c:462`), si `mqtt_parse_cfg()`
falla se incrementa `s_mqtt_cfg_rejected`, se loguea local y **no se manda
ningún ack**. El único ack de cfg que existe es el de éxito, con `res`
hardcodeado en `"ok"` (línea 476).

**Consecuencia si no se arregla.** Desde el servidor, una cfg que el panel no
pudo parsear es **indistinguible** de un panel dormido, de uno sin señal y de un
mensaje que se perdió. El operador ve "enviada, sin confirmar" y no hay forma de
saber si conviene esperar o corregir. El contador vive en el equipo, donde nadie
lo mira.

**Propuesta.** Emitir `up t:ack` con `res:"error"` y un `det` corto (`"parse"`,
o el campo que falló) cuando `mqtt_parse_cfg` rechaza. El `cfg_v` puede ir en 0
si no se pudo leer. El lado servidor ya lo soporta: `gtd.confirm_config` acepta
`res`/`det` y marca la cfg como fallida con su detalle — hoy ese camino existe y
nunca se ejercita porque el firmware no lo usa.

**Prioridad:** media (no rompe nada, pero convierte un silencio ambiguo en un
diagnóstico).

---

## F6 · `mq_pub_cfg_full` descarta el espejo en silencio si no entra

**Problema.** `mq_pub_cfg_full()` (`task_mqtt.c:300-314`): si el documento
truncó (`n <= 0`), **limpia el flag `dirty`**, incrementa `s_mqtt_pub_fail` y
retorna. No publica nada y nadie afuera se entera.

El comentario explica bien por qué se limpia el flag (dejarlo prendido
reintentaría cada 100 ms para siempre) — la decisión es correcta, lo que falta es
avisar.

**Consecuencia si no se arregla.** Un `cmd t:refresh` puede no producir
absolutamente nada. Del lado servidor eso se ve como "el panel no contestó", y la
configuración queda "sin verificar" para siempre sobre un equipo que está
perfecto. Es el peor caso: un panel sano que parece roto, sin ninguna pista.

**Propuesta.** Cuando el `cfg_full` no entre, emitir un `up` mínimo avisándolo
—por ejemplo `{"t":"cfg_full","truncado":true,"cfg_v":N}`— o incluirlo en el
`tele` como un contador visible. Alcanza con que el servidor sepa que el espejo
quedó viejo *a propósito* y no por un mensaje perdido.

**Prioridad:** media (afecta el diagnóstico, no la operación).

---

## F7 · El ejemplo de `tele.modulos.eeprom.kb` contradice al código (solo doc)

**Qué pasa.** `mqtt_payload.c` arma el campo como `size_bytes / 1024u`: son
**kilobytes**, y un AT24C32 (4096 B) reporta `"kb":4`. El ejemplo de
`docs/mqtt_design.md` muestra `"eeprom":{"slot":0,"kb":32,...}`, que solo sería
cierto si `kb` fueran kilobits.

**Consecuencia.** Nos costó un bug en producción: la web tomó el ejemplo como
contrato, leyó 4 como kilobits (512 B) y calculó un techo de **14 vecinos** en
lugar de 126. Los paneles del Barrio Docente marcaban 11 y 7 controles como
"no entran" con la EEPROM prácticamente vacía. Arreglado del lado web
(`capacidadDeRegistros`), pero el próximo que lea el doc va a caer igual.

**Propuesta.** Corregir el ejemplo a `"kb":4` y aclarar la unidad al lado del
campo. Sin cambios de código.

**Prioridad:** baja (documentación), pero el error que induce es caro.

---

*Contacto: los docs 07 (integración) y el contrato en el repo web
(`docs/contrato-gtd-postgres.md`) tienen el contexto completo. F4-F6 salieron de
implementar la pantalla de configuración por equipo; el diseño está en
`docs/superpowers/specs/2026-08-04-configuracion-por-equipo-design.md` del repo
web.*
