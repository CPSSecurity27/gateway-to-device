# 01 — Contrato MQTT (firmware ↔ GtD)

**El punto de sincronización.** Este documento describe el contrato tal como lo
implementa el firmware. `src/gtd/domain/` es su espejo en Python.

> ⚠️ **Estado:** el contrato está **implementado en el firmware** (componente
> `mqtt_av` + task_mqtt), pero **NO verificado contra un broker real ni en
> hardware**. Puede haber ajustes al probar. Nada acá es "planificado a futuro":
> todo lo que se lista existe hoy en el código (excepción explícita: la op
> `t:rf` "sync", marcada como no implementada más abajo).

## Guía rápida de recepción (¿a qué me suscribo y qué recibo?)

El GtD se suscribe a **3 canales**, todos QoS 1. Los otros dos (`cfg`, `cmd`) son
de **bajada**: los publica el GtD, no se suscribe.

```
SUBSCRIBE  av/+/status    → presencia del panel (online | durmiendo | offline/LWT)
SUBSCRIBE  av/+/tele      → snapshot de telemetría (voltajes, modo, cfg_v, ...)
SUBSCRIBE  av/+/up        → stream de eventos; el subtipo va en el campo "t"
```

| Llega en... | Es... | Qué hace el GtD |
|---|---|---|
| `av/<mac>/status` | presencia / LWT | upsert `panel_state` (online/offline) |
| `av/<mac>/tele` | telemetría | upsert `panel_state` (snapshot) |
| `av/<mac>/up` `"t":"alarma"` | una alarma disparada | insert `evento` (dedup por `eid`) + confirma comando si trae `cid` |
| `av/<mac>/up` `"t":"ack"` | resultado de un cmd/cfg | confirma comando (`cid`) o cfg (`cfg_v`) |
| `av/<mac>/up` `"t":"scan"` | resultado de scan WiFi | insert `evento` |
| `av/<mac>/up` `"t":"ota"` | progreso de OTA | insert `evento` |

- **¿De qué panel es?** La `<mac>` está en el tópico (`av/<mac>/...`).
- **¿Qué tipo de evento es?** El campo `"t"` dentro del payload de `up`.
- El detalle de cada payload (campos, ejemplos) está más abajo en este documento.

## Fuente de verdad (repo del firmware AlarmaV6)

| Aspecto | Archivo del firmware |
|---|---|
| Payloads D→S (subida) | `components/mqtt_av/mqtt_payload.h` / `.c` |
| Payloads S→D (cfg/cmd, bajada) | `components/mqtt_av/mqtt_parse.h` / `.c` |
| Slugs de modo y origen de alarma | `components/alarma_core/alarma_core.h` |
| Constantes (schema, root, prefijo) | `components/mqtt_av/mqtt_config.h` |
| Diseño narrativo | `docs/mqtt_design.md`, `docs/alarma_design.md` |

> Cuando el GtD viva en otro repo, estas rutas apuntan al repo del firmware. Al
> cambiar el firmware, actualizar `src/gtd/domain/` y `tests/test_payloads.py`.

## Tópicos

Raíz `av` (`MQTT_TOPIC_ROOT`). Identidad `AV-<MAC>` (ver [02](02-provisioning-auth.md)).

**El `<id>` del tópico es el string completo `AV-<MAC-hex-mayúsculas>`** — con el
prefijo, sin dos puntos: `av/AV-240AC4000110/status`. Verificado contra una placa
real el 2026-07-24; antes esto no estaba escrito y era ambiguo.

> **No cambiar sin leer esto.** El `<id>` es **idéntico** al usuario MQTT, y de eso
> depende que la ACL del broker sea una regla `pattern av/%u/…` para toda la flota
> en vez de cinco líneas por panel. Si el id y el usuario divergen, la ACL pasa a
> crecer con la flota y hay que mantenerla sincronizada a mano.
> Ver `deploy/gtd.acl`.

| Tópico | Dir | Retained | QoS | Contenido |
|---|---|---|---|---|
| `av/<id>/status` | D→S | sí | 1 | presencia + LWT + aviso de sueño |
| `av/<id>/tele` | D→S | sí | 1 | snapshot de telemetría |
| `av/<id>/up` | D→S | no | 1 | stream de subida, discriminado por `"t"` |
| `av/<id>/cfg` | S→D | sí | 1 | estado deseado completo (cfg_v única) |
| `av/<id>/cmd` | S→D | no | 1 | órdenes con `cid` |
| `av/all/cmd` | S→D | no | 1 | broadcast a toda la flota |

El GtD **se suscribe** a `av/+/{status,tele,up}` y **publica** en `av/<id>/{cmd,cfg}`.

## Envelope común (todo mensaje D→S)

```json
{ "v": 1, "ts": 1700000000, "tsq": 2, ... }
```

- `v` = `MQTT_SCHEMA_V` (hoy **1**). Un `v` distinto ⇒ descartar (esquema incompatible).
- `ts` = unix UTC del panel. `tsq` = calidad de la hora (`rtc_time_quality_t`).

## Subida (D→S)

### status
```json
// online
{"v":1,"estado":"online","fw":"6.0.0","modo":"ACTIVE_240","durmio":{"wake":1699999000},"ts":...,"tsq":...}
// durmiendo
{"v":1,"estado":"durmiendo","despierta":1700003600,"causa":"MODEM_SLEEP_REPORTE","ts":...,"tsq":...}
// offline (LWT, lo publica el broker si el equipo muere)
{"v":1,"estado":"offline","causa":"lwt"}
```
`estado` ∈ `online | durmiendo | offline`. `durmio`/`despierta` son opcionales.

### tele (snapshot retained)
```json
{"v":1,"ts":...,"tsq":...,
 "energia":{"modo":"ACTIVE_240","vbat":12.60,"vpanel":18.30,"vfuente":13.80},
 "ota":{"fw":"6.0.0","estado":0,"ultimo":0},
 "alarma":{"mode":"off","act":3,"redisp":0,...},
 "cfg_v":7}
```
Pueden aparecer más sub-objetos/campos. El GtD guarda el documento **crudo
completo** en `panel_state.energia`/JSONB y extrae los indexados (`modo`,
`alarma.mode`, `cfg_v`, `rf.gen`).

> **`rf.gen` va ANIDADA** (`"rf":{…,"gen":9}`), como en el `cfg_full`. El GtD la
> leía como un campo `rf_gen` de primer nivel que el firmware no manda nunca, así
> que `panel_state.rf_gen` se quedaba en 0 para todos los paneles y la detección
> de "este equipo se quedó atrás con la base de controles" comparaba contra un
> cero fijo. Corregido el 2026-08-05 (`TeleMsg.rf_gen` es un `@property`).

### up (stream, discriminado por `"t"`)

`"t"` ∈ `alarma | ack | scan | ota | cfg_full | rf_rx | rf_rx_end | audit |
audit_detalle`.

> **Cerrado (2026-08-05).** La brecha que decía este doc —`rf_rx`, `rf_rx_end`,
> `audit` y `audit_detalle` descartados con `PayloadError`— **ya no existe**:
> los cuatro están en `UpType`/`_UP_MODELS` y se guardan como eventos crudos.
> Lo que todavía no hay es quién los INTERPRETE: `audit` (los hashes por DNI que
> permiten detectar qué controles difieren de verdad contra la base del panel)
> se guarda pero nadie lo compara. Ver §"Base RF" abajo.

**alarma** — reporte canónico de `task_alarma` (resultado real de una activación):
```json
{"v":1,"t":"alarma","eid":"<boot_id>-<seq>","mode":"emergency","prev":"off",
 "origin":"rf","dni":12345678,"codigos":2,"ts":...,"tsq":...}
```
- `mode`/`prev`/`origin` son **slugs** (nunca números). `prev` siempre presente:
  activación (`prev="off"`), cambio, re-disparo (`prev==mode`), apagado.
- `dni`/`codigos`: solo si `origin="rf"`. `cid`: solo si `origin="mqtt"`.
  `rol`: solo si `origin="portal"`.
- **`eid` da idempotencia** (QoS1 redistribuye): un mismo `(mac, eid)` se procesa
  una sola vez.

**ack** — resultado de un cmd (`cid`) o de una cfg (`cfg_v`):
```json
{"v":1,"t":"ack","cid":"cmd-xyz","res":"ok","det":"encolado","ts":...,"tsq":...}
{"v":1,"t":"ack","cfg_v":7,"res":"ok","ts":...,"tsq":...}
```
`res` ∈ `ok | error`. Cierra el ciclo del downlink (marca `commands.confirmed`).

> **El ack de cfg SOLO existe cuando la cfg se aplicó, y siempre con `res:"ok"`.**
> Una cfg con `cfg_v` vieja/igual se ignora en **silencio total** — ni ack de
> error (`task_mqtt.c:469`, solo incrementa `mqtt_cfg_rejected`). Una cfg
> malformada, tampoco. El único mecanismo de detección del lado servidor es el
> timeout: publiqué `cfg_v=N` y no llegó su ack. Un ejemplo anterior de este doc
> mostraba un `res:"error"`/`det:"cfg_v vieja"` que **no existe en el firmware**.

**scan** / **ota**:
```json
{"v":1,"t":"scan","redes":[{...}],"ts":...,"tsq":...}
{"v":1,"t":"ota","estado":2,"resultado":0,"fw":"6.0.1","ts":...,"tsq":...}
```

## Slugs

**Modo de alarma** (`alarma_core.h`): `off`, `suspicious`, `alert`, `emergency`,
`fire`, `medical`, `silent`, `panic`.

**Origen** (`alarma_origin_slug`): `rf`, `mqtt`, `auto`, `portal`.

## Bajada (S→D)

### cmd (órdenes con `cid`)
```json
{"t":"alarma","cid":"cmd-xyz","mode":"emergency"}
```
`t` ∈ `estado | restart | alarma | scan | test | ota | factory | rf | refresh |
hora | i2c_scan | red | cal` (`mqtt_cmd_type_t`). El `cid` correlaciona el comando
con su `ack`/`up`. El GtD arma el payload desde `commands.payload` (lo produce el
backend de app); no lo interpreta.

> **`"op":"sync"` NO existe: se ELIMINÓ del firmware** (`portal_design §1.10`
> cerró la §12 de mqtt_design). Este doc lo daba por "planificado" y no lo está:
> la carga masiva por snapshot HTTPS se descartó a favor de deltas + auditoría.
> Las sub-ops vivas son `batch | del | monitor | probe | audit | query`
> (`mqtt_rf_op_t`), y `sync` cae en el `default` que ackea "op invalida".

#### Base RF (`t:rf`) — lo que hay que saber antes de mandar una

```json
{"t":"rf","cid":"cmd-…","op":"batch","gen":41,
 "clientes":[{"dni":30111222,"codigos":[123456,234567]}]}
```

- **La base se indexa por DNI**, no por control: un registro es una PERSONA con
  hasta 4 códigos (`EE_CODES_PER_CLIENT`), y entran ~126 en un AT24C32.
- **`op:batch` es alta PURA**: si el DNI ya existe —o si alguno de los códigos ya
  es de otro— devuelve `EE_DUP` y **aborta el lote entero**. Actualizar a alguien
  es `del` y después `batch`, en ese orden.
- **5 clientes por comando** (`EE_SAVE_BATCH_MAX`), ~2,25 s cada uno: cada alta
  barre la EEPROM.
- **`gen` es obligatorio en la práctica.** `get_u32` devuelve 0 sin marcar error
  para una clave ausente, así que un comando sin `gen` deja al panel reportando
  generación 0 — que es lo que reporta un equipo recién vuelto de fábrica.
- El `det` del ack trae `ee_status N`: `1` no existe · `2` **base llena** · `6`
  duplicado · `8` la cola EEPROM no respondió.
- **El panel recuerda 8 `cid`** (`MQTT_CID_RING_N`): publicar 24 lotes en ráfaga
  desborda su dedup. La web los encola pero los libera de a uno (estado `queued`
  en `commands`, ver [03](03-data-model.md)).

### cfg (estado deseado, retained)
```json
{"cfg_v":8, "redes":[...], "modulos":{...}, "tiempos":{...}, "mante":{"on":false}, ...}
```
- `cfg_v` es obligatorio y es el **árbitro de versión** (el panel rechaza cfg
  vieja). Sin `cfg_v` ⇒ malformado.
- **Lleva secretos** (passwords WiFi en `redes`) → cifrar en reposo, no loguear.
- Campos: ver `mqtt_cfg_msg_t` en `mqtt_parse.h` (redes, modulos, tiempos, hora/tz,
  mante, alarma.autooff, **`red_avanzada`**/roam, central/alias/ubicacion/grupo).
  Tabla completa con tipos, unidades, rangos y defaults de fábrica en
  [05](05-preguntas-equipo-web.md) §2.
- **`red_av` no es una clave JSON**: es el nombre del campo del struct en C
  (`has_red_av`). La clave es **`red_avanzada` en los dos sentidos** —
  `mqtt_parse.c:154` (baja) y `task_mqtt.c:267` (`cfg_full`, sube).
- **El merge es por sección, no por campo.** Una cfg parcial es válida (cada
  sección tiene su `has_*`), pero si mandás `modulos` o `central`, mandalos
  **completos**: los subcampos ausentes toman su default, no el valor actual —
  `{"modulos":{"rf":true}}` apaga ds3231/eeprom/supervisor. `redes` reemplaza el
  set entero. Ver [05](05-preguntas-equipo-web.md) §4.

## Idempotencia (resumen)

| Mecanismo | Dónde | Para qué |
|---|---|---|
| `eid` = `<boot_id>-<seq>` | up t:alarma | dedup de alarmas |
| `cid` | cmd ↔ up t:ack/alarma | correlación comando→confirmación |
| `cfg_v` | cfg ↔ up t:ack | versión de config deseada vs aplicada |
