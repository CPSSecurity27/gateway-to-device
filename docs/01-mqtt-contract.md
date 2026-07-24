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
`alarma.mode`, `cfg_v`, `rf_gen`).

### up (stream, discriminado por `"t"`)

`"t"` ∈ `alarma | ack | scan | ota`.

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
{"v":1,"t":"ack","cfg_v":7,"res":"error","det":"cfg_v vieja","ts":...,"tsq":...}
```
`res` ∈ `ok | error`. Cierra el ciclo del downlink (marca `commands.confirmed`).

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

> **No implementado (planificado):** la sub-op `t:rf` `"op":"sync"` (sincronización
> masiva de base RF por snapshot) — el firmware responde "no implementado". Es una
> decisión abierta (mqtt_design §12). El resto de las sub-ops de `t:rf`
> (batch/del/monitor/probe/audit/query) sí existen.

### cfg (estado deseado, retained)
```json
{"cfg_v":8, "redes":[...], "modulos":{...}, "tiempos":{...}, "mante":{"on":false}, ...}
```
- `cfg_v` es obligatorio y es el **árbitro de versión** (el panel rechaza cfg
  vieja). Sin `cfg_v` ⇒ malformado.
- **Lleva secretos** (passwords WiFi en `redes`) → cifrar en reposo, no loguear.
- Campos: ver `mqtt_cfg_msg_t` en `mqtt_parse.h` (redes, modulos, tiempos, hora/tz,
  mante, autooff[8], red_av/roam, central/alias/ubicacion/grupo).

## Idempotencia (resumen)

| Mecanismo | Dónde | Para qué |
|---|---|---|
| `eid` = `<boot_id>-<seq>` | up t:alarma | dedup de alarmas |
| `cid` | cmd ↔ up t:ack/alarma | correlación comando→confirmación |
| `cfg_v` | cfg ↔ up t:ack | versión de config deseada vs aplicada |
