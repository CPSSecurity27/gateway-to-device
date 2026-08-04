# 06 — Respuesta a la guía de PgRepo (2026-08-03)

> **De:** equipo GtD · **Para:** equipo web CPS
> **Responde a:** `gtd-guia-implementacion.md` (2026-08-03)
> **Estado:** de acuerdo con el diseño. Antes de escribir `PgRepo` necesitamos
> cerrar **1 bug nuestro** y **8 puntos del contrato**, de los cuales 2 son
> bloqueantes.

Todo lo de acá está verificado contra el código de este repo, con archivo y línea.
Donde decimos "lo tomamos nosotros", es trabajo nuestro y no espera respuesta.

---

## 0. Veredicto: adelante

Reemplazar `panel_state` y `eventos` por funciones contra sus tablas nos parece
**mejor** que nuestro `001_init.sql`, no un compromiso. El argumento es correcto y
lo compartimos: un cambio de mapeo pasa a ser una migración de ustedes y no un
deploy coordinado de dos servicios. Y que lo respalden con permisos (`cps_alarms`
sin INSERT/UPDATE sobre `device_state` y `event`) es la forma correcta de hacerlo:
un acuerdo que el motor no impone se rompe solo, tarde o temprano.

Las tres cosas que habíamos pedido que se mantuvieran están las tres:

1. `insert_evento` devuelve `bool` (dedup por `eid`). ✅
2. `upsert_config_espejo` arbitra por `cfg_v`. ✅
3. Canales `gtd_commands` / `gtd_config` con la MAC como payload. ✅

Y confirmamos que `PgRepo` queda como esperábamos: un envoltorio de una línea por
método sobre el `Protocol Repo` (`src/gtd/db/repo.py:19-50`). Los pipelines de
uplink y downlink **no se tocan**.

También registramos y agradecemos lo de §8 de su guía: `NOTIFY` filtrado por
cambio real, `av/all/cmd` fuera de la ACL, `red_avanzada` en los dos sentidos,
merge contra el espejo con rechazo del patch si no hay espejo, y `cfg_v`
estrictamente mayor. Los cinco quedaron como los habíamos propuesto.

---

## 1. El bug: la MAC no significa lo mismo de los dos lados

**Esto es nuestro, lo arreglamos nosotros, pero lo documentamos acá porque el
malentendido salió de un archivo que escribimos nosotros y les pudo costar caro.**

Su §5.1 dice: MAC = **12 hex mayúsculas, sin `AV-`**. Correcto y no lo discutimos:
la MAC es un dato del equipo, `AV-` es una convención de tópico MQTT nuestra.

El problema es que nuestro código usa hoy el `device_id` completo —con prefijo—
como clave, en las dos direcciones:

| Dirección | Qué pasa hoy | Consecuencia |
|---|---|---|
| Subida | `topics.parse()` (`src/gtd/mqtt/topics.py:29`) devuelve `parts[1]` crudo, o sea `AV-A842E38FCA6C`, y `uplink.py:82` se lo pasa tal cual a `repo.upsert_panel_state()` | **Todas** las llamadas devolverían `unknown_device`. Y como las funciones no tiran excepción, el GtD loguearía normal y la base quedaría vacía |
| Bajada | `downlink.py:31` toma la MAC del `NOTIFY` (12 hex, sin prefijo) y arma `av/A842E38FCA6C/cmd` | Ningún panel está suscripto ahí. La ACL lo permite (`topic write av/+/cmd`), así que el broker **acepta el PUBLISH y lo tira al vacío** |

O sea: falla silenciosa en las dos direcciones, y en la de bajada sin ni siquiera
un error del broker. Podría habernos llevado días de producción descubrirlo.

De dónde salió: `migrations/001_init.sql:16` dice
`mac TEXT PRIMARY KEY, -- AV-XXXXXXXXXXXX (12 hex)`, que se contradice a sí mismo.
Ustedes leyeron "12 hex", nosotros escribíamos el string completo, y nuestros
tests lo fosilizaron (`MAC = "AV-240AC4000110"` en tres archivos de `tests/`).
Perdón, ese lo comimos nosotros otra vez.

**Cómo lo arreglamos** (trabajo nuestro, sin impacto para ustedes):

- `topics.parse()` pasa a devolver la MAC normalizada, validando 12 hex
  mayúsculas. De paso descarta tópicos con id inválido, que hoy entran.
- `cmd_topic()` / `cfg_topic()` reponen el prefijo al publicar.
- Helpers explícitos en `src/gtd/domain/contract.py`, al lado de
  `DEVICE_ID_PREFIX`, para que la traducción nunca vuelva a quedar implícita.
- De ese borde para adentro (presencia, repo, logs) todo habla MAC pelada.

**El provisioning y la ACL no se tocan:** ahí la identidad MQTT sigue siendo
`AV-<MAC>` (usuario = client_id = `<id>` del tópico), que es lo que hace posible
la regla `pattern` única para toda la flota.

---

## 2. Lo que nos falta de su contrato

Ocho puntos. Los dos primeros son **bloqueantes** para que el downlink sea
confiable; el resto son mejoras o dudas de una línea. Cada uno dice qué hacemos
si la respuesta es "no", así que ninguno nos frena para empezar.

### P0-1 · `fetch_pending_macs()` — nos piden un barrido y no hay con qué

Su §4 dice, y tienen toda la razón:

> "Un `NOTIFY` puede perderse si el listener estaba reconectando. Al levantar la
> conexión, hagan un barrido inicial de pendientes en vez de confiar solo en el
> evento."

Estamos de acuerdo. **Pero las ocho funciones son todas por MAC**
(`fetch_pending_commands(p_mac)`, `fetch_pending_config(p_mac)`) y el rol
`cps_alarms` no tiene `SELECT` sobre las tablas de `gtd`. No tenemos forma de
preguntar *"¿qué paneles tienen algo pendiente?"*.

`LISTEN/NOTIFY` no tiene memoria: si la web inserta un comando a las 03:00 y el
GtD está reiniciándose por un deploy, ese `NOTIFY` se disparó al vacío y **no
vuelve nunca**. La fila queda `pending` para siempre.

Y no es solo el arranque. Hay un tercer caso que ya existe en nuestro código:
`downlink.handle` (`src/gtd/pipeline/downlink.py:31-33`) hace `fetch` → `publish`
→ `mark_sent`. Si el PUBLISH falla en el medio (MQTT caído), el comando queda
`pending`, el `NOTIFY` ya se consumió, y no sale nunca más hasta que alguien toque
la fila a mano.

**Pedimos:**

```sql
gtd.fetch_pending_macs()  -- setof (mac TEXT, canal TEXT)
                          -- canal ∈ 'gtd_commands' | 'gtd_config'
```

Con eso el barrido es **una** query, y la corremos en tres momentos: al arrancar,
al reconectar a Postgres, y al reconectar al broker.

**Si dicen que no:** el rol tiene `SELECT` sobre `public.*`, así que podríamos
listar la flota entera e iterar panel por panel. Con 1.000 paneles son 2.000
round-trips en cada reconexión, para que en el 99,9% de los casos no haya nada.
Funciona, pero es feo y no escala. Preferimos la función.

### P0-2 · No hay canal para reportar que una `cfg` no se pudo entregar

Ustedes mismos marcan el riesgo de `MQTT_IN_PAYLOAD_MAX = 1024` (§7.4 de su guía,
y coincidimos). Cuando lo detectemos del lado nuestro —una `cfg` de 5 redes con
passwords largas que no entra en el buffer de entrada del panel— tenemos dos
opciones, las dos malas:

- Llamar `mark_config_sent()`: es mentira, la cfg nunca va a llegar.
- No llamar nada: la fila queda `pending` y el `NOTIFY` va a volver a disparar en
  cada UPDATE, en un loop inútil.

Para comandos tenemos salida: podemos usar `confirm_command(cid, res => 'error',
det => '…')`. **Para `cfg` no hay ningún camino de vuelta.** La web va a ver
"enviada, sin ack" y no va a poder distinguir *"el panel está dormido"* de *"el
payload no entra y nunca va a entrar"*.

**Pedimos** una de las dos:

```sql
gtd.mark_config_sent(p_mac TEXT, p_cfg_v BIGINT,
                     p_res TEXT DEFAULT 'ok', p_det TEXT DEFAULT NULL)
-- o bien
gtd.mark_config_failed(p_mac TEXT, p_cfg_v BIGINT, p_det TEXT)
```

Nos da igual cuál. Lo que necesitamos es poder decir *por qué* no salió.

**Si dicen que no:** lo logueamos como ERROR de nuestro lado y no publicamos. La
cfg queda `pending` en la base sin explicación y alguien la va a tener que
diagnosticar mirando el journal del GtD.

### P1-3 · `last_seen` con `tsq` malo va a mentir

Hoy `uplink.py:95` manda `last_seen = model.ts`, que es el reloj **del panel**.

En nuestro doc 05 §8 les dijimos que con `tsq >= 2` (`FROM_NVS`, `INTERNAL_ONLY`,
`UNRELIABLE`) ese `ts` puede estar horas o días atrasado. Consecuencia directa: un
panel sin NTP ni DS3231, conectado y hablando **ahora mismo**, va a aparecer en el
tablero como "visto por última vez hace 3 días".

`last_seen` no es un dato del panel: es *cuándo lo escuchamos nosotros*. Es un
dato del servidor.

**Proponemos** que `upsert_panel_state` ponga `last_seen = now()` internamente e
ignore `p_last_seen`, o que lo dejen como está y nosotros mandemos `NULL` ahí. Si
además quieren el reloj declarado por el panel (para auditar deriva), lo mandamos
en un parámetro aparte junto con su `tsq`, que es el único modo de interpretarlo.

**Si prefieren dejarlo:** lo resolvemos nosotros mandando `model.ts` solo cuando
`tsq <= 1` y la hora de recepción cuando no. Funciona, pero nos parece que el dato
tiene que salir del reloj del servidor y no de una heurística nuestra.

### P1-4 · `durmiendo` entra a la base como `offline`

`src/gtd/domain/models.py:41` define `online = (estado == "online")`. El canal
`status` tiene **tres** estados: `online`, `durmiendo` y `offline` (LWT). O sea,
un panel en sueño programado —que avisó que se iba **y hasta cuándo**, en el campo
`despierta`— llega a `device_state` como `online = false`, indistinguible de uno
que se cayó.

Para el monitoreo la diferencia importa mucho: *"se cayó a las 3 AM"* versus
*"duerme hasta las 7"*. Nosotros ya usamos esa distinción internamente: el
watchdog de presencia no marca caído a un panel dormido, salvo que pase su hora de
despertar con margen y siga mudo (`src/gtd/pipeline/presencia.py:108-134`). Ese
matiz hoy se pierde al cruzar a la base.

**Pregunta:** ¿`device_state` tiene lugar para un tercer estado, o para un
`sleep_until TIMESTAMPTZ`? Si lo agregan, se lo mandamos (el `despierta` viaja en
el payload y hoy lo tiramos).

**Si dicen que no:** lo dejamos como está y lo anotamos como pérdida conocida de
información. No nos bloquea.

### P2-5 · Orden del parámetro `fw`, y notación nombrada

Su §7.2 nos pide agregar `fw` a `upsert_panel_state`. **Lo hacemos** — lo tenemos
en `StatusMsg.fw` (`models.py:35`) y es trivial. Dos cosas para coordinar:

1. **Que el parámetro vaya al final de la firma.** Cualquier otra posición rompe a
   quien llame posicionalmente.
2. Nosotros vamos a llamar con **notación nombrada**
   (`SELECT gtd.upsert_panel_state(p_mac => $1, p_online => $2, …)`) en vez de
   posicional como en su ejemplo de §4. Cuesta lo mismo, mantiene el
   "NULL = no tocar" igual de explícito (mandamos todos los parámetros siempre) y
   nos desacopla del orden para la próxima vez.

**Confirmen:** ¿la firma vieja sigue funcionando entre que ustedes despliegan la
nueva y nosotros el código que la usa? Con `DEFAULT NULL` al final debería, pero
queremos que quede dicho.

### P2-6 · ¿Hay pgbouncer entre el GtD y Postgres?

Si lo hay en modo `transaction`, asyncpg necesita `statement_cache_size=0` o los
prepared statements fallan de forma **intermitente** — el peor modo de falla
posible para diagnosticar a distancia.

Si lo hay, además nos afecta el `PgListener`: `LISTEN` necesita una conexión real
y estable, no una del pool de pgbouncer. Tendríamos que conectarnos directo al
Postgres para ese canal.

**Contesten:** ¿directo o con pooler? Si es con pooler, ¿nos dan un DSN directo
para la conexión del listener?

### P2-7 · ¿Quieren el `cfg_full` también por `insert_evento`?

Hoy, cuando llega un `up t:cfg_full`, llamamos **solo** `upsert_config_espejo`
(`uplink.py:124`): el espejo es estado, no un evento. Pero su §3.2 lista
`cfg_full` entre los tipos válidos de `insert_evento`, así que quedó la duda.

**Pregunta:** ¿alcanza con el espejo, o quieren además el historial en
`uplink_raw` (para poder ver *cómo cambió* la config de un panel en el tiempo)?
Es una línea de código para nosotros; solo díganlo.

### P2-8 · El DSN de desarrollo

Para correr sus 14 casos de §6 desde nuestro lado —con `asyncpg`, con el códec de
`jsonb` puesto, con el rol `cps_alarms` real— antes de tocar producción. Sin eso
escribimos `PgRepo` a ciegas y lo probamos por primera vez el día del despliegue.

Con eso podemos verificar de punta a punta: `unknown_device` sin excepción, el
"NULL = no tocar", el `false` del dedup y el `stale` post-`factory`.

---

## 3. Lo que tomamos nosotros

Esto es trabajo nuestro. Lo listamos para que sepan qué esperar y en qué orden.

**a) Normalización de MAC** (§1 de este doc). Primero, porque bloquea todo lo
demás y no depende de ustedes.

**b) Que una caída de Postgres no mate al GtD.** Hoy `__main__.py:110` solo atrapa
`except* aiomqtt.MqttError`. Un `asyncpg.PostgresConnectionError` desde el uplink
sube por el `TaskGroup` y **termina el proceso**. Con `StubRepo` era imposible;
con Postgres pasa a ser el modo de falla más probable.

Y hay algo peor debajo: aiomqtt ya mandó el PUBACK cuando el mensaje llega a
nuestro handler, así que un fallo de base **pierde el mensaje**. Para
`status`/`tele` da lo mismo (son retained, viene otro en 5 minutos). Para
`up t:alarma` no da lo mismo.

Vamos a hacer: reintento con backoff dentro de `PgRepo` para errores de conexión,
sin matar el loop, más un **spool en disco** (JSONL append-only, drenado al
recuperar) solo para el canal `up`. Es el mismo agujero que nosotros les
señalamos en el firmware (doc 05 §6: alarma sin PUBACK al momento del reboot);
sería incoherente dejarlo abierto de este lado.

**c) Usar el `bool` de `insert_evento`.** Hoy lo ignoramos (`uplink.py:114-118`):
en una alarma redistribuida por QoS 1 llamamos `confirm_command` dos veces. Es el
dato que pedimos y no lo estamos usando. Lo cortamos ahí y logueamos el dedup.

**d) Guarda de 1024 bytes en el downlink**, antes del PUBLISH, con log explícito.
Gratis, y evita el "no llega el ack y nadie sabe por qué". Va de la mano de P0-2.

**e) `PgListener` con reconexión de verdad:** conexión dedicada, ping periódico
(una conexión asyncpg muerta no avisa), reconexión con backoff, barrido al
levantar (→ P0-1) y **colapso de `NOTIFY` repetidos por MAC** — el fetch es por
MAC, así que cinco notificaciones seguidas del mismo panel son un solo trabajo.

**f) Sus dos pedidos de §7.1 y §7.2:** el parámetro `fw`, y los cuatro tipos que
hoy descartamos (`rf_rx`, `rf_rx_end`, `audit`, `audit_detalle`). Para estos
últimos tenemos que ir al repo del firmware a sacar la forma exacta de los
payloads. Los mandaremos por `insert_evento` como cualquier otro, tal como piden.
No es esta semana, pero está en la lista.

---

## 4. Una observación sobre su §9 (cifrado en reposo)

Cifrar `panel_config` en Postgres **no alcanza** para sacar las passwords WiFi de
circulación en claro.

El GtD publica la `cfg` **retenida** en el broker (`downlink.py:42`, `retain=True`
— es correcto, es estado deseado y el panel lo tiene que tomar al conectar). Eso
significa que el payload con `redes[].psw` queda escrito en el disco de Mosquitto,
sin fecha de vencimiento, hasta que se publique una `cfg` nueva sobre ese tópico.

Está protegido por la ACL —solo ese panel (`pattern read av/%u/cfg`) y el usuario
`gateway` pueden leerlo— así que no es una urgencia. Pero conviene saberlo antes
de dar DT2 por cerrado: cuando cifren la base, **el eslabón débil pasa a ser el
broker**, no Postgres.

Si en algún momento quieren cerrarlo del todo, hay que hablarlo con firmware: la
única solución limpia es que la `cfg` no viaje retenida y el panel la pida al
conectar (`cmd t:refresh` invertido), o que las passwords viajen cifradas con una
clave derivada del equipo. Las dos son cambios de firmware, no de ninguno de los
dos servidores.

---

## 5. Plan que proponemos

| # | Qué | Depende de |
|---|---|---|
| 1 | Normalización de MAC + tests | nadie — arrancamos ya |
| 2 | Este documento a ustedes | — |
| 3 | `PgRepo` + `PgListener` reales (`StubRepo` queda como fallback con `GTD_PG_DSN` vacío) | P0-1, P2-6, P2-8 |
| 4 | Resiliencia: no morir con la base caída, spool del canal `up`, guarda de 1024 | P0-2 |
| 5 | `fw` + los cuatro tipos nuevos | P2-5 |

Los pasos 1 y 2 son inmediatos. El 3 lo podemos empezar contra la base de
desarrollo apenas tengamos el DSN, aunque `fetch_pending_macs` todavía no exista
(el barrido lo dejamos detrás de una bandera).

---

## Apéndice — Checklist para responder

Para que puedan contestar punto por punto sin releer todo:

| # | Pregunta | Bloquea |
|---|---|---|
| P0-1 | ¿Agregan `gtd.fetch_pending_macs()`? | downlink confiable |
| P0-2 | ¿Cómo reportamos una `cfg` que no se pudo entregar? | diagnóstico de cfg |
| P1-3 | `last_seen`: ¿lo pone `now()` del servidor o lo seguimos mandando? | no |
| P1-4 | ¿`device_state` distingue `durmiendo` de `offline`? | no |
| P2-5 | `fw` al final de la firma, ¿y la firma vieja convive? | no |
| P2-6 | ¿Postgres directo o con pgbouncer? Si hay pooler, ¿DSN directo para el listener? | `PgListener` |
| P2-7 | ¿`cfg_full` también por `insert_evento`, o alcanza el espejo? | no |
| P2-8 | ¿Nos pasan el DSN de desarrollo con el rol `cps_alarms`? | probar antes de prod |

Y del lado nuestro, lo que quedó pendiente de antes y sigue igual: el `SALT_MQTT`
de producción (PA4) sigue siendo lo único que bloquea el alta masiva por
derivación. Para probar el camino completo hoy alcanza con el interín de
`PANEL_PASSWORD` explícita, que no requiere el salt.
