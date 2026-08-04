# 05 — Respuestas al equipo web (2026-08-03)

Todo lo de acá está verificado contra el código, no contra los docs. Fuente:
repo del firmware `AlarmaESP32V6_05-03-2026` + este repo. Donde el doc viejo
decía otra cosa, lo digo explícitamente.

---

## 1. `mqtt_config.h` — se los mando, pero no es el archivo que necesitan

`components/mqtt_av/mqtt_config.h` son los **parámetros del cliente MQTT**
(keepalive, backoff, tamaños de buffer). **No tiene ni una sola clave de `cfg`.**
Está completo en el apéndice A al final.

El esquema de configuración vive repartido en tres lugares:

| Qué | Dónde |
|---|---|
| Claves, subcampos y tipos que el panel **acepta** | `components/mqtt_av/mqtt_parse.h` (`mqtt_cfg_msg_t`) + `mqtt_parse.c` (`mqtt_parse_cfg`) |
| Rangos / clamps | repartidos: `mqtt_parse.c`, `task_wifi.c::app_roam_set`, `alarma_config.h`, `alarma_core.c` |
| Defaults de fábrica | `alarma_core.c::CATALOG`, `wifi_config.h`, `rtc_config.h`, `mqtt_config.h`, inicializadores de `app_portal.c`/`task_rf.c` |

Como eso no se puede "mandar como archivo", abajo va la tabla consolidada. Es la
respuesta real a la pregunta.

## 2. Esquema de `cfg` (bajada, S→D) — tabla completa

Envelope: `cfg_v` es **obligatorio** y `> 0`. Sin `cfg_v`, o con `cfg_v: 0`, el
documento es malformado y se descarta entero.

| Clave | Subcampo | Tipo | Unidad | Rango aceptado | Default de fábrica |
|---|---|---|---|---|---|
| `cfg_v` | — | uint32 | — | `1..2^32-1` (0 = malformado) | `0` (NVS vacía) |
| `redes[]` | `ssid` | string | — | ≤ 31 chars (buffer 32) | — |
| | `psw` | string | — | ≤ 63 chars (buffer 64) | — |
| | `prio` | uint | — | `1..5`; fuera de rango ⇒ se asigna por orden en el array | — |
| | *(máx. 5 entradas: `WIFI_MAX_PROFILES`; las extra se ignoran en silencio)* | | | | |
| `modulos` | `ds3231` | bool | — | — | **`false`** |
| | `eeprom` | bool | — | — | **`false`** |
| | `supervisor` | bool | — | — | **`false`** |
| | `rf` | bool | — | — | **`true`** (fail-safe: un equipo sordo al pánico es el peor estado) |
| | `eeprom_slot` | uint | — | `0..1` (se hace `& 1`) | `0` |
| `tiempos` | `send_tele_s` | uint32 | segundos | clamp duro a `[30, 86400]` | `300` (`MQTT_TELE_PERIOD_S_DEFAULT`) |
| `hora` | `tz_offset_s` | int32 | segundos | `[-50400, +50400]` (±14 h). Fuera ⇒ **cfg entera malformada** | `-10800` (`RTC_UTC_OFFSET_S`) |
| `mante` | `on` | bool | — | — | `false` (y auto-salida a las 4 h, `MANTE_AUTO_EXIT_S`) |
| `alarma.autooff` | `suspicious` | uint32 | segundos | clamp `[120, 1800]`; `0`/ausente = no tocar | `120` |
| | `alert` | uint32 | segundos | idem | `300` |
| | `emergency` | uint32 | segundos | idem | `600` |
| | `fire` | uint32 | segundos | idem | `600` |
| | `medical` | uint32 | segundos | idem | `600` |
| | `silent` | uint32 | segundos | idem | `600` |
| | `panic` | uint32 | segundos | idem | `900` |
| `red_avanzada` | `roam_rssi` | int32 | dBm | clamp `[-90, -50]` | `-72` |
| | `roam_delta` | uint32 | dBm | clamp `[5, 30]` | `10` |
| | `roam_cooldown_s` | uint32 | segundos | clamp `[60, 3600]` | `300` |
| | *(los tres son obligatorios si mandan el objeto; falta uno ⇒ cfg entera malformada)* | | | | |
| `central` | `alias` | string | — | ≤ 31 chars | `""` |
| | `ubicacion` | string | — | ≤ 63 chars | `""` |
| | `grupo` | string | — | ≤ 15 chars | `""` |

Notas de rango que importan para la UI:

- **Los clamps no rechazan: recortan y ackean `ok`.** Si mandan `send_tele_s: 5`,
  el panel guarda `30`, contesta `ok` y el `cfg_full` posterior muestra `30`. La
  UI tiene que reconciliar contra el `cfg_full`, no asumir que lo enviado quedó.
- Los **rangos que sí rechazan** (y tiran la cfg **completa**, sin ack): `cfg_v`
  ausente o 0, `tz_offset_s` fuera de ±14 h, `red_avanzada` incompleto, y
  cualquier campo con el **tipo** equivocado (un `"30"` string donde va número).

## 3. `red_av` vs `red_avanzada`, y `alarma`/`cal`/`rf`/`id`

**`red_av` no existe como clave JSON en ningún lado.** Es el nombre del campo del
struct en C (`has_red_av` en `mqtt_cfg_msg_t`). La clave JSON es **`red_avanzada`
en los dos sentidos** — verificado en `mqtt_parse.c:154` (bajada) y
`task_mqtt.c:267` (subida). Doc vieja: `docs/01-mqtt-contract.md` §cfg listaba
`red_av/roam`, ya lo corregí en este repo. Perdón, ese lo comimos nosotros.

Sobre las otras cuatro:

| Clave | En `cfg` (baja) | En `cfg_full` (sube) | ¿A propósito? |
|---|---|---|---|
| `alarma` | **sí** (`alarma.autooff`) | sí (`alarma.autooff`) | simétrico, el doc no lo listaba bien |
| `cal` | **no** | sí (`bat`/`panel`/`fuente` → `{m, b}`) | **sí, a propósito.** La calibración se escribe con `cmd t:cal` (`op` ∈ `set`\|`auto`\|`reset`), no por cfg. En `cfg_full` es solo lectura |
| `rf` | **no** | sí (`total_codigos`, `gen`) | **sí, a propósito.** La base RF se maneja con `cmd t:rf` (batch/del) y `rf_gen`. En `cfg_full` es solo lectura |
| `id` | **no** | sí (`dev`, `fw`) | **sí, a propósito.** Identidad, solo lectura |

**Y hay una asimetría real que ustedes no marcaron y es la que sí molesta:**
`central` (`alias`/`ubicacion`/`grupo`) **baja pero no vuelve** — el `cfg_full`
del firmware no lo espeja (`task_mqtt.c:255-291`). O sea: hoy el panel web no
tiene forma de verificar que el alias/ubicación/grupo se aplicaron. Es un bug del
firmware, no del diseño. Lo levantamos con el equipo de firmware.

## 4. ¿Parcial o documento completo? → **merge, por sección**

Confirmado, su supuesto es correcto: `publish_config` hace **merge**. Cada
sección tiene su flag `has_*` y solo se aplica si la clave está presente
(`task_mqtt.c::mq_apply_cfg`, líneas 424-460). Una cfg con solo
`{"cfg_v": 9, "mante": {"on": true}}` es válida y no toca nada más.

**Pero el merge es a nivel sección, no a nivel campo.** Tres trampas, todas con
la misma forma — si mandan el objeto, mándenlo **completo**:

1. **`modulos` — la peor.** Los subcampos ausentes toman su default, no el valor
   actual. `{"modulos": {"rf": true}}` **apaga ds3231, eeprom y supervisor**
   (default `false`), sin error y con ack `ok`.
2. **`central`.** Subcampo ausente ⇒ string vacío ⇒ **borra** el valor guardado.
   `{"central": {"alias": "Casa 12"}}` deja `ubicacion` y `grupo` en `""`.
3. **`redes` reemplaza el set completo.** No es un merge por SSID: lo que mandan
   es la lista entera de hasta 5 perfiles. Omitir una red la borra.

Las que sí mergean bien campo a campo: `alarma.autooff` (cada modo es
independiente, `0`/ausente = no tocar) y `red_avanzada` (aunque exige los 3).

Recomendación concreta para la firma: `publish_config(mac, patch)` que hace merge
**contra el último `cfg_full` recibido** antes de publicar, y rellena `modulos`,
`central` y `redes` completos siempre. Si no tienen `cfg_full` todavía para ese
panel (equipo que nunca conectó), rechacen el patch parcial en vez de adivinar —
mandar `modulos` a ciegas apaga módulos.

## 5. ¿Qué hace el panel con un `cfg_v` menor?

**Lo ignora, y lo ignora en silencio total: no manda ack, ni `ok` ni `error`.**

```c
// task_mqtt.c:469
if (!mqtt_cfgv_should_apply(s_mq_cfg_v, s_cfg.cfg_v)) {
    s_mqtt_cfg_rejected++;   // solo incrementa un contador de telemetría
    return;                  // ni ack (idempotencia §5.4)
}
```

La regla es **estrictamente mayor** (`mqtt_engine.c:74`, tests E07):
`12 → 13` aplica; `13 → 13` ignora; `13 → 12` ignora; `0 → 1` aplica.

Consecuencias para ustedes:

- **Reenviar la misma `cfg_v` es un no-op silencioso.** Si necesitan forzar que
  el panel reaplique, hay que **subir `cfg_v`**, no republicar. (O usar
  `cmd t:refresh`, que solo pide que republique su `cfg_full` — no reaplica.)
- **No esperen un ack negativo por versión vieja.** Solo hay ack de cfg cuando
  se aplicó, y siempre con `res: "ok"`. El único timeout que pueden implementar
  es: publiqué `cfg_v=N`, no llegó `up t:ack {cfg_v: N, res: "ok"}` en X segundos
  ⇒ no se aplicó, por la razón que sea.
- El `cfg_v` aplicado **persiste en NVS** (sobrevive reboots). Un `factory` lo
  vuelve a 0. Ojo con eso: después de un factory, si su `cfg_v` en la base sigue
  en 40, el panel acepta 41 sin problema — pero si ustedes reinician el contador
  a 1, también entra. No dependan de la monotonía global, dependan del `cfg_v`
  que reporta el `tele`/`cfg_full` del panel.
- El doc `01-mqtt-contract.md` mostraba un ejemplo
  `{"t":"ack","cfg_v":7,"res":"error","det":"cfg_v vieja"}`. **Ese mensaje no
  existe en el firmware.** Ya lo corregí en el doc. Buena que preguntaron.

## 6. `eid` = `<boot_id>-<seq>` — sí, el `boot_id` cambia, y no importa

Las dos mitades:

- **Sí, cambia en cada arranque.** `s_mqtt_boot_id = esp_random() | 1u`
  (`task_mqtt.c:825`), truncado a 24 bits al formatear
  (`mqtt_engine.c:81`). Sobrevive un stop/start de `task_mqtt` dentro del mismo
  boot, no un reboot. El `seq` arranca en 1 en cada boot.
- **Pero el escenario que les preocupa no puede pasar con este firmware.** No hay
  reenvío a través de un reboot. La cola de salida (`g_mqtt_out`) es una cola
  FreeRTOS **en RAM**, y el `eid` se acuña **en el momento de publicar**, no al
  encolar (`task_mqtt.c:966`). Un panel que se reinicia no tiene nada que
  reenviar: la cola se fue con el RAM. El dedup por `(mac, eid)` cubre
  exactamente lo que tiene que cubrir — la redistribución QoS 1 dentro de una
  sesión.

**El modo de falla real es el inverso y es peor: pérdida, no duplicado.** Una
alarma aceptada por el outbox de esp-mqtt pero sin PUBACK al momento del reboot
**se pierde entera**. No hay persistencia. Si les importa (a nosotros sí), eso es
un ticket para firmware, no un índice más en la base.

Riesgo residual de colisión de `eid`: `boot_id` es aleatorio de 24 bits, así que
dos boots del mismo panel colisionan con probabilidad ~1/16.7M **y además** haría
falta el mismo `seq`. Despreciable. **No agreguen dedup semántico** (por
`mode`+`origin`+`dni` en ventana de tiempo): el re-disparo legítimo existe y
viaja con `prev == mode`. Lo colapsarían.

## 7. `dni` — no está en el control, lo pusieron ustedes

El control RF transmite un **código de 64 bits**, nada más. El panel busca ese
código en su base RF local (EEPROM) y de ahí saca el `dni`
(`task_rf.c:229-236` → `alarma_core.c:181` → `task_mqtt.c:978`).

Esa base RF la carga **el servidor**, con `cmd t:rf` `op:"batch"` (hasta 5
clientes por lote, 4 códigos por cliente). O sea: **el `dni` que les vuelve en la
alarma es el que ustedes mismos cargaron.** Es su propia clave, redundada por el
panel. Para identificar al vecino, el join es contra su tabla de clientes.

- Si el código **no** está en la base del panel, la alarma **no se dispara**: no
  hay evento, solo un log local. Nunca van a recibir un `dni` desconocido.
- `codigos` es la **cantidad de códigos registrados de ese cliente** (1..4), no
  cuál se apretó. El botón sí determina el modo (`a`=emergency, `b`=suspicious,
  `c`=alert, `d`=off) pero la posición no viaja en `t:alarma`.
- **Viene solo cuando hay `dni > 0`**, que en la práctica es `origin: "rf"`. El
  emisor lo escribe con la condición `if (dni > 0)` (`mqtt_payload.c:243`), no
  mirando el origen — pero `dni` solo se puebla en el camino RF. Para los otros
  orígenes viaja en su lugar: `cid` (origin `mqtt`) o `rol` ∈ `tec`\|`cps`
  (origin `portal`). `origin: "auto"` (auto-off) no trae ninguno.

## 8. `tsq` — escala 0..4, **menor es mejor**

`rtc_time_quality_t`, en `components/system_types/rtc_types.h`:

| `tsq` | Significado | ¿Confían en el `ts` del panel? |
|---|---|---|
| `0` | `SYNCED_NTP` — NTP reciente | **Sí.** Precisión de red |
| `1` | `FROM_DS3231` — RTC con batería, sin NTP reciente | **Sí.** Deriva de cristal, segundos por semana |
| `2` | `FROM_NVS` — piso guardado en NVS, sin NTP ni DS3231 | **No.** El `ts` es un piso, puede estar horas o días atrasado |
| `3` | `INTERNAL_ONLY` — RTC interno sin corregir | **No** |
| `4` | `UNRELIABLE` — más de 6 h sin sync (`RTC_MAX_UNSYNC_ERROR_S = 21600`) | **No** |

**Umbral que les recomiendo: `tsq <= 1` ⇒ ordenar por `ts` del panel; `tsq >= 2`
⇒ ordenar por hora de recepción del servidor.**

Dos matices que les van a ahorrar un bug:

- El arranque MQTT está **gateado por reloj plausible** (`>= 2024-01-01 UTC`,
  `task_mqtt.c:20`) para que no falle el handshake TLS con "certificate is not
  yet valid". Así que un panel conectado nunca les manda un `ts` de 1970. **Pero
  el gate mira el valor, no la calidad** — con `tsq: 2` el `ts` pasa el gate y
  aun así puede estar muy atrasado. No usen "el ts es plausible" como sustituto
  de `tsq`.
- El `tsq` viaja en **todos** los mensajes D→S, no solo en las alarmas. Guárdenlo
  junto al evento; sin él no pueden re-ordenar a posteriori.

## 9. `trg_panel_state_notify` — de acuerdo, pero el volumen es 60× menor

**Aceptamos la propuesta: notificar solo ante cambio real.** El riesgo que
describen es correcto y la falla es fea (la cola de `pg_notify` llena hace fallar
el `COMMIT`, no solo la notificación).

Un dato que cambia la urgencia: **no hay un NOTIFY por heartbeat.** El keepalive
MQTT (`MQTT_KEEPALIVE_S = 20`) es PINGREQ/PINGRESP entre panel y broker — el GtD
ni se entera, no genera escritura. Lo que escribe `panel_state` es:

- `tele`, cada `send_tele_s` (**default 300 s**, piso 30 s), más disparos por
  cambio de estado
- `status`, en conexión / sueño / LWT
- el watchdog de presencia del GtD cuando marca offline

O sea ~1 escritura por panel cada 5 minutos en régimen. Con 1.000 paneles son
~3,3 NOTIFY/s, no una avalancha. Igual el filtro es correcto y gratis.

Propuesta concreta, con la advertencia que va abajo:

```sql
CREATE OR REPLACE FUNCTION notify_app_panel_state() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT'
       OR NEW.online      IS DISTINCT FROM OLD.online
       OR NEW.alarma_mode IS DISTINCT FROM OLD.alarma_mode
       OR NEW.cfg_v       IS DISTINCT FROM OLD.cfg_v
       OR NEW.rf_gen      IS DISTINCT FROM OLD.rf_gen
       OR NEW.modo_energia IS DISTINCT FROM OLD.modo_energia
    THEN
        PERFORM pg_notify('app_panel_state', NEW.mac);
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;
```

(La comparación va en el cuerpo, no en un `WHEN`: en `INSERT` no existe `OLD`.)

**Lo que quedan perdiendo:** con ese filtro, un cambio de **solo voltaje**
(`energia`) o de `last_seen` no despierta a la app. Si el dashboard muestra
tensión de batería en vivo, o "visto hace N minutos", eso deja de actualizarse
solo. Dos opciones: agregan `energia` al filtro (vuelve a ser 1 notify por tele,
que es el volumen real y es tolerable), o la app poll-ea `panel_state` cada N
segundos para lo no-crítico y usa el NOTIFY solo para alarma/presencia. Nosotros
preferimos la segunda: el NOTIFY es para lo que no puede esperar.

Y una cosa que **no** filtren nunca: `eventos`. Ahí no hay trigger hoy, pero si
lo agregan, una alarma no puede depender de un `IS DISTINCT FROM`.

## 10. LWT — sí publica, pero `mark_offline` **no sobra**

Sí, el LWT publica exactamente eso, retenido, QoS 1, en `av/<id>/status`:

```c
// mqtt_payload.h:24
#define MQTT_LWT_PAYLOAD "{\"v\":1,\"estado\":\"offline\",\"causa\":\"lwt\"}"
// mqtt_transport.c:211 — topic = status, qos = 1, retain = 1
```

**Pero el LWT no alcanza, y esto lo medimos contra una placa real sobre
Starlink**, no es teoría:

1. **Takeover de sesión.** El panel reconecta desde otra IP (CGNAT de Starlink
   rebindea) antes de que el broker note que la sesión anterior murió. Mosquitto
   expulsa la vieja y **no publica el LWT**. El panel republica `online`. Para el
   servidor, el equipo nunca se fue. Medición: **31 cortes del broker, apenas
   unos pocos visibles** en el log del GtD.
2. **Panel que enmudece sin que venza el keepalive.** Queda `online` para
   siempre.

Por eso el GtD tiene un watchdog de presencia (`src/gtd/pipeline/presencia.py` +
`__main__.py::_watchdog_presencia`) que mide lo único que no miente: **cuándo
habló por última vez**. Detecta silencio (marca `online = false`), reconexiones
invisibles y las cuenta.

Así que: **el LWT es el camino feliz, `mark_offline` es la red.** Mantengan la
función. Detalle de implementación que sí importa: nuestro watchdog corre
**dentro** del TaskGroup de la conexión MQTT a propósito — si el GtD pierde el
broker, toda la flota parecería callada y los marcaría offline en masa. Al morir
con la conexión, eso no puede pasar. Si ustedes mueven esa lógica a una función
SQL con un cron, **pierden esa protección**: un GtD caído les va a marcar toda la
flota offline. Si van por ahí, necesitan un heartbeat del propio GtD en la base y
que la función lo respete.

## 11. `av/all/cmd` — de acuerdo, no lo usamos

Sin objeción, y de hecho **el GtD nunca publica ahí hoy**: `downlink.handle` solo
publica `topics.cmd_topic(mac)` (`av/<id>/cmd`), por panel. El broadcast existe
en el firmware (los paneles **están suscriptos**, `mqtt_transport.c:135`) y la
ACL le da permiso de escritura al usuario `gateway`, pero no hay ningún camino de
código que lo use.

Su razón (multi-inquilino por barrio) es la correcta y es la misma que ya está
escrita en el principio D10 del repo: la multi-inquilinidad vive en Postgres, no
en tópicos MQTT. Un broadcast por MQTT saltearía toda esa capa.

**Propuesta:** lo dejamos como está —firmware suscripto, GtD sin usarlo— y
sacamos `topic write av/all/cmd` de la ACL del usuario `gateway`
(`docs/02-provisioning-auth.md:116`). Así el "no lo usamos" queda **aplicado por
el broker**, no por disciplina. Si algún día hace falta un broadcast real
(un `restart` de flota en una ventana de mantenimiento), se agrega la línea a
mano en ese momento.

## 12. `migrations/001_init.sql` — vigente y **nunca aplicado**

Sin cambios desde el commit inicial (`e3b9876`), árbol limpio. Sigue siendo el
contrato tal como está escrito.

Y algo importante: **Postgres todavía no existe en el servidor.** Ese `.sql`
nunca se corrió contra nada. Hoy el GtD corre con `StubRepo` (in-memory + logs)
mientras `GTD_PG_DSN` esté vacío. O sea que **no hay migración que hacer ni datos
que preservar** — están en la posición más cómoda posible para rediseñarlo.

**Adelante con reemplazar `panel_state` y `eventos` por funciones contra sus
tablas.** El GtD no toca SQL: habla contra el `Protocol Repo`
(`src/gtd/db/repo.py`). Mientras `PgRepo` cumpla esas 8 firmas, los pipelines no
se enteran de cómo son sus tablas. Las tres cosas que sí necesitamos que se
mantengan, sea con la forma que sea:

1. **`insert_evento` devuelve `bool`**: `false` si el `eid` ya existía. Es el
   dedup y el GtD lo usa. `INSERT ... ON CONFLICT DO NOTHING RETURNING`.
2. **`upsert_config_espejo` arbitra por `cfg_v`**: no pisar el espejo con una
   versión más vieja que la guardada.
3. **Los canales `gtd_commands` y `gtd_config` con la `mac` como payload.** Es
   como el GtD se entera de que hay algo para bajar. Si cambian el nombre del
   canal, avísennos: están en `src/gtd/db/listener.py`.

`panel_config.payload` lleva **passwords WiFi en claro** (`redes[].psw`). Cifrado
en reposo es de ustedes (DT2 en `04-decisions.md`) y sigue abierto.

## 13. PA4 / `SALT_MQTT` de producción — **sigue bloqueado**

Sin avance. Estado exacto al 2026-08-03:

- **El algoritmo está cerrado y verificado contra el código**, no contra un doc:
  `wifi_manager.c::wifi_manager_get_role_secret` emite
  `HMAC-SHA256(SALT_MQTT, MAC_STA)[0..11]` formateado `%02X` ⇒ **24 hex
  mayúsculas, sin prefijo**. El `SCPS-` que figuraba en el doc 02 era un error
  nuestro, ya corregido.
- **Lo único que falta es el secreto.** El repo solo tiene el placeholder
  (marcado `_IS_PLACEHOLDER` en `wifi_config.h`), y **no reproduce** el vector de
  verificación: da `C50ED5A77C1B…` donde el vector dice `4EA453D7…`. Eso es
  tranquilizador, no alarmante — confirma que el vector se generó con el salt
  real, que por diseño no está en el repositorio.
- **Para cerrarlo hace falta una acción humana, no código:** quien compiló el
  build de producción tiene que entregar por canal seguro el `SALT_MQTT` que
  inyectó por `-D`. Se verifica con `deploy/diag-salt.sh`, que lo contrasta
  contra el vector **sin registrar nada en ningún lado**.
- **Interín para que no queden bloqueados ustedes:** se puede dar de alta un
  panel con la password del vector explícita
  (`PANEL_PASSWORD=… deploy/provision-panel.sh`). Sirve para una MAC concreta y
  no requiere el salt. Con eso pueden probar el camino completo hoy mismo, con
  una placa. Lo que no se puede sin el salt es dar de alta **por derivación**, o
  sea el alta masiva.

---

## Bonus: un bug nuestro que encontramos contestando esto

El firmware emite tipos de `up` que el GtD **no conoce y descarta hoy**:
`rf_rx`, `rf_rx_end`, `audit`, `audit_detalle` (`task_mqtt.c:325, 364, 391, 994`).
El GtD los tira con `PayloadError: up con t desconocido` y un `log.warning`.

No los bloquea a ustedes ahora mismo (son de monitor RF y auditoría de base RF,
funciones de instalador), pero si el panel web va a tener pantalla de alta de
controles RF, **esos cuatro tipos son justamente los que la alimentan**. Lo
tomamos nosotros: hay que agregarlos a `UpType` y a `_UP_MODELS` en
`src/gtd/domain/`, y decidir si van a `eventos` o a una tabla propia. Avisen si
lo necesitan pronto y lo priorizamos.

---

## Apéndice A — `components/mqtt_av/mqtt_config.h` completo

```c
#pragma once

/*
 * mqtt_config.h — parámetros del cliente MQTT (R1a).
 *
 * Esquema de 5 tópicos por equipo bajo av/<id>/. QoS 1 en todo, retained en
 * status/tele/cfg, LWT en status. MQTT 5 + TLS. Ver docs/mqtt_design.md.
 *
 * MQTT_CMD_EXPIRY_S y MQTT_OTA_EXPIRY_S se eliminaron en R1a: documentaban una
 * política de message-expiry que ningún código llegó a aplicar (cero
 * consumidores en todo el árbol). Si se implementa, vuelven acá.
 */

#include "system_config.h"   /* SERVER_HOST */

#define MQTT_BROKER_URI            "mqtts://" SERVER_HOST ":8883"
#define MQTT_TOPIC_ROOT            "av"     /* raíz: av/<id>/<canal>          */
#define MQTT_DEVICE_ID_PREFIX      "AV-"    /* + 6 bytes de la MAC STA en hex */
#define MQTT_SCHEMA_V              1        /* campo "v" de todos los payloads */

/* Keepalive: latido de aplicación (PINGREQ/PINGRESP, 2 B cada uno). Semántica
 * REAL de esp-mqtt (process_keepalive, IDF 5.5.3), que NO es la del estándar:
 *   - manda PINGREQ a keepalive/2 de silencio  → 10 s
 *   - declara la conexión muerta a keepalive   → 20 s
 * keepalive_tick solo se resetea con CONNACK y con PINGRESP: los publish no
 * cuentan, es un latido puro. Detección de caída = entre 10 y 20 s.
 *
 * El valor es un COMPROMISO A DOS PUNTAS: también es lo que el broker tolera
 * de silencio antes de darnos por muertos y disparar el LWT (1,5x = 30 s).
 * Bajarlo detecta más rápido pero vuelve más nervioso el "equipo offline" del
 * dashboard — sobre Starlink, donde los hipos de 20-30 s son normales, eso
 * son falsos caídos. 20 s es el punto medio: detección ~11 s más rápida que
 * los 30 s originales sin bajar la tolerancia del broker de 45 a 22 s. */
#define MQTT_KEEPALIVE_S           20       /* modos activos                   */
#define MQTT_RECONNECT_MS          30000    /* PISO de esp-mqtt entre intentos;
                                               el backoff adaptativo de abajo
                                               lo acelera cuando corresponde   */
#define MQTT_NETWORK_TIMEOUT_MS    10000    /* explícito (= default). NO bajarlo:
                                               también es el tope del handshake
                                               TLS, y con el jitter de Starlink
                                               abortaría handshakes legítimos  */

/* ── Backoff adaptativo de reconexión (Starlink / CGNAT) ────────────────────
 * esp-mqtt espera MQTT_RECONNECT_MS FIJOS entre intentos. Eso está bien si el
 * broker se cayó, pero el modo de falla real acá es otro: Starlink rebindea el
 * NAT, la IP pública cambia y la conexión TCP establecida queda huérfana
 * (muere el 4-tuple). El broker NUNCA estuvo caído — un intento inmediato
 * conecta a la primera. Esperar 30 s ahí es ceguera pura: medido en HW, 35-51 s
 * entre la caída y la reconexión, casi todo espera inútil.
 *
 * Escalera de espera por intento consecutivo fallido. El primer escalón es 0:
 * reconexión inmediata, que es el caso del rebind. Si ESA falla, entonces sí
 * el problema es otro y se escala. El tope iguala a MQTT_RECONNECT_MS: de ahí
 * en más da igual quién dispare, esp-mqtt o nosotros. */
#define MQTT_BACKOFF_LADDER_MS     { 0, 2000, 5000, 15000, 30000 }

/* Una sesión que aguantó esto se considera SANA y resetea la escalera a cero.
 * Sin este piso, un broker que acepta y patea al instante (auth rota, ACL mal
 * puesta) recibiría un martilleo de handshakes TLS — exactamente lo que los
 * 30 s fijos venían a evitar. */
#define MQTT_HEALTHY_SESSION_MS    60000
#define MQTT_SESSION_EXPIRY_S      86400    /* 24 h: cmd sobreviven el sueño   */
#define MQTT_QUIESCENCE_MS         2500     /* silencio que cierra la misión   */
#define MQTT_TELE_PERIOD_S_DEFAULT 300      /* tele periódica (cfg tiempos)    */
#define MQTT_TELE_MIN_GAP_MS       30000    /* [A-02] piso entre teles por dirty:
                                               task_wifi marca dirty cada tick
                                               (500 ms) — sin este piso, tele se
                                               publicaba 2 veces por segundo    */

#define MQTT_PAYLOAD_TX_MAX        2048     /* buffer de armado (cfg_full manda:
                                               5 redes con passwords ≈ 1,2-1,6 KB
                                               — portal_design §7.3)            */
#define MQTT_IN_PAYLOAD_MAX        1024     /* payload entrante aceptado       */
#define MQTT_IN_QUEUE_DEPTH        4        /* handler esp-mqtt → task_mqtt    */
#define MQTT_OUT_QUEUE_DEPTH       16       /* otras tasks → task_mqtt         */
#define MQTT_CID_RING_N            8        /* últimos cid recordados (dedupe) */
#define MQTT_CID_MAXLEN            24
#define MQTT_ACK_SETTLE_MS         1500     /* espera para que el ack salga
                                               antes de restart/factory        */
```

**Ojo con `MQTT_IN_PAYLOAD_MAX = 1024`.** Es el tope de payload **entrante** que
el panel acepta. Una `cfg` con 5 redes y passwords largas puede pasarse: el
buffer de **salida** para `cfg_full` es 2048 justamente porque eso ronda los
1,2-1,6 KB. **Una cfg completa de 5 redes puede no entrar en el panel.** No lo
probamos todavía; si arman una cfg grande y no llega ack, es por acá. Vale la
pena que lo verifiquemos juntos antes de que lo descubran en producción.
