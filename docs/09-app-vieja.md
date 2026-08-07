# 09 — El puente con la app vieja

**TEMPORAL.** Existe solo mientras haya clientes que no actualizaron la app de
vecinos. Todo el legado vive en `src/gtd/legacy/`, en una unidad de systemd
propia y en una sola migración del repo web (`LegacyAppBridge`), justamente para
que el día del apagado sea una tarde y no una arqueología.

> ✅ **EN PRODUCCIÓN desde el 2026-08-07.** `broker-bridge` está apagado y
> deshabilitado; `cps-legacy-app` corre en su lugar, con **push DESACTIVADO**
> (`GTD_LEGACY_PUSH=false`) mientras dure la prueba. Los dos paneles `AlarmaV6`
> están en el BANCO, así que una activación desde la app vieja hace sonar el
> banco y no la central del barrio.
>
> Antes del corte hubo que **resolver 60 eventos abiertos** del banco: sin eso
> la proyección arrancaba escribiendo `Estado: Activada` y los 49 vecinos veían
> una emergencia falsa, una de ellas del día anterior.
>
> **Falta para terminar:** encender el push (probándolo antes contra un tópico
> de prueba), las reglas RTDB de solo lectura, y el inventario de centrales.

## El problema

Hay clientes que no van a actualizar. La app que tienen instalada
(`com.cpssecurity.app_alarma` v4.0.0+4, proyecto Firebase `cpssecurityapp`) es
un APK ya distribuido: **su contrato no se puede cambiar**. Y las alarmas que
manejaba —las centrales `CENTRALVECINAL05/06`— se reemplazan por paneles
`AlarmaV6`, que hablan otro protocolo, en otro puerto, con otra base.

## El contrato de la app vieja (relevado del código, no supuesto)

Sale de `lib/main.dart` del repo de la app, commit `acf2f55`. Cuatro superficies:

| # | Qué | Cómo |
|---|---|---|
| 1 | **Login** | Lee `ClientesID/<DNI>` de la RTDB. **Sin contraseña**: si el nodo existe, entrás |
| 2 | **Activación** | MQTT anónimo a `mqtt.cpssecurity.com.ar:1883`, tópico `cliente/servidor`, QoS 1 |
| 3 | **Estado vivo** | Listeners RTDB sobre `<Marcador>/DatosCentral/Estado`, `<Marcador>/Instrucciones/InstruccionesActivacion` y `.../Historial/*` |
| 4 | **Push** | FCM al tópico **crudo del marcador** (`CENTRALVECINAL05`) |

El payload de la 2, que es el que consume este puente:

```json
{"cliente_id":"44679351","modo_a":"cps003",
 "gps":{"longitud":"-64.865000","latitud":"-24.233000"}}
```

Tres detalles del original que hay que respetar tal cual:

- **`gps` puede no venir**: si no hay fix en 2 s la app publica sin el campo.
- **Las coordenadas son STRING**, con 6 decimales, longitud primero.
- La app se autolimita a 1 mensaje por segundo. **No le creemos**: eso lo cumple
  la app real, no cualquiera que publique en un tópico abierto.

## Cómo funciona ahora

```
app vieja ──1883 anónimo──> [gtd.legacy] ──enqueue_legacy_alarm──> gtd.commands
                                                                        │
                                                              GtD ──8883 TLS──> panel
                                                                        │
   event con activador y GPS <── gtd.insert_evento <── up t:alarma {cid} ──┘
```

**El adaptador es tonto a propósito**: parsea el JSON y llama a una función. No
elige a qué equipo le pega, no valida cupos, no lee tablas, no escribe SQL. Toda
la decisión vive en `gtd.enqueue_legacy_alarm`, en un solo lugar.

### El `cid` es el hilo que ata la persona con el evento

El problema es de correlación. El que sabe QUIÉN activó es el adaptador, cuando
recibe el MQTT; el que crea el `event` es el panel, segundos después. Y **el
firmware no reenvía el DNI**: solo lo manda cuando `origin="rf"` (ver
[01](01-mqtt-contract.md)). Con `origin="mqtt"` lo único que vuelve es el `cid`.

Ese `cid` ya viajaba entero hasta `insert_evento` —`pipeline/uplink.py` le pasa
el documento completo— así que la correlación salió sin cambiarle la firma a
nada. El adaptador deja `(cid → dni, gps)` en `gtd.legacy_activation` y la
función lo levanta **antes** de insertar: el evento nace completo o no nace.
No es un UPDATE posterior porque `event` es append-only, y una segunda pasada
dejaría al monitoreo viendo una emergencia sin dueño.

### Quién es "legacy"

Lo dice `device.legacy_marker`. Un hogar es alcanzable desde la app vieja si —y
solo si— su alarma preferida tiene marcador. **Sin listas congeladas**: crece y
se achica sola con los datos. Los `CENTRALVECINAL05/06` quedaron mapeados a sus
paneles nuevos en la migración de datos del 2026-08-06.

### El desarme

`cps999` sale como `t:alarma mode:off`. Como un `mode:off` **no crea evento**
(cae en el dead letter como `desarme`), sin ayuda las emergencias de la app
vieja quedaban `OPEN` para siempre: el vecino con la app vieja no tiene panel
web, ese botón es su único cierre.

`gtd.resolve_on_disarm` cierra **cualquier evento abierto del equipo, sin exigir
que lo cierre quien lo abrió** — en una alarma de barrio el que la apaga casi
nunca es el que la disparó (decisión del 2026-08-07). Registra quién cerró. Se
cierra al RECIBIR el reporte del panel y no al encolar el comando: si el equipo
está caído, cerrar antes dejaría el tablero diciendo "resuelto" con la sirena
sonando.

> **No es solo de la puerta vieja.** La primera versión (`close_legacy_events`)
> sí lo era, y eso resultó ser un bug: el **botón D del control remoto** no
> cerraba nada, porque el firmware solo manda `cid` cuando `origin='mqtt'` y
> aquella función lo exigía. Corregido el mismo día por
> `ResolveOnDisarm`, que sirve a los tres caminos (control, panel web y app
> vieja) y **excluye el `auto`**: el apagado por temporizador de la sirena no
> significa que terminó la emergencia.

## La bajada: qué ve la app

Postgres es la única verdad. Firebase pasa a ser una **proyección de solo
lectura** que existe nada más que para que la app vieja siga viendo lo que
espera. La escribe el mismo proceso, en un segundo bucle: `LISTEN app_event`
para las emergencias (tienen que aparecer ya) y barrido para el catálogo.

Tres cosas que salieron de mirar la base de producción el 2026-08-07 y que **no
se deducen de ningún doc**:

**1. `Estado` gobierna si se ve algo.** La app hace
`if (estado == 'Activada' && activation != null)`. Si ese string no dice
exactamente eso, los datos se escriben bien y **el vecino no ve nada**. El
vocabulario es cerrado: `Conectada` (verde), `Activada` (naranja), cualquier
otra cosa (gris). Se deriva de `device_state.online` + eventos abiertos, y una
emergencia abierta gana sobre el equipo caído.

**2. La app muestra el modo CRUDO**, sin traducir (`Text(activation.modoalarma)`).
Lo que se escriba ahí es literalmente lo que lee el vecino. En producción hoy
conviven `cps001` (lo que dejaban las activaciones por control RF) con
`SOSPECHOSO` (las de la app): el vecino ve "cps001" la mitad de las veces. Ahora
todas pasan por la misma proyección y usan el catálogo nuevo (`Ladrón`,
`Incendio`…), así el mismo evento se lee igual en el panel y en el teléfono.

**3. La clave del historial NO es ISO-8601**: es `DD-MM-YYYYTHH:MM:SSZ`, con el
día primero. La app la parsea con un regex y, si no matchea, cae en
`DateTime.now()` — o sea que un formato equivocado no falla: muestra todo el
historial como si fuera de recién.

Los valores existentes están entrecomillados (`"\"Mza 17-B casa 24\""`), pero
`_clean()` de la app borra las comillas antes de mostrar, así que se escribe
limpio.

### El catálogo `ClientesID`

Es lo que corta la deriva: sin esto, un vecino cargado desde el panel web no
puede entrar a la app vieja (su login es "existe este nodo") y una suspensión
hecha en el panel nunca le llega.

Se escribe con **PATCH y no con PUT**: el nodo tiene campos que no manejamos
—`Inicio`, que escribe la propia app al loguearse, y `ControlRF`, que quedó de
la carga original— y un PUT los borraría. La excepción es `familia`, que sí va
con PUT: si a alguien lo dieron de baja, su clave `usuarioN` tiene que
desaparecer, y un merge la dejaría para siempre.

### El ensayo

`GTD_LEGACY_RTDB_PREFIJO` escribe todo abajo de un nodo que la app no lee. **Es
la única forma de probar sin mostrarle una activación falsa a un barrio
entero**: la app tiene listeners abiertos sobre los paths reales, así que un
PATCH de prueba aparece en los teléfonos al instante.

## La seguridad, dicha en voz alta

**El listener 1883 es anónimo y está abierto** (`deploy/legacy-1883.acl`:
`topic readwrite #`), y **la app vieja no tiene autenticación**: su login es
"existe este DNI". O sea que cualquiera con internet puede publicar una
activación a nombre de cualquier DNI.

Eso ya era verdad con el `broker-bridge`. Lo que cambia es que ahora el sistema
pasa de decir *"sonó una alarma"* a afirmar *"la disparó Fulano, teléfono tal,
desde estas coordenadas"* — algo que no puede verificar. Es justamente el dato
que alguien va a mirar cuando algo salga mal.

No tiene arreglo sin tocar la app. Lo que sí se hizo:

- **La función no acepta destino.** Siempre es la alarma preferida del hogar de
  ese DNI, y encima tiene que tener `legacy_marker`. Un mensaje anónimo no
  elige a qué equipo le pega.
- **Valida todo**: el DNI existe, está `ACTIVE`, tiene `home_member` `ACTIVE` y
  el hogar está activo. Nueve motivos de rechazo distintos, todos logueados.
- **Freno anti-abuso** por DNI y global (`freno.py`). El `cps999` no se frena
  nunca: apagar una alarma tiene que funcionar siempre.
- **El rol `cps_legacy` tiene EXECUTE sobre una función y nada más.** No lee
  `device`, y **no puede llamar a `close_legacy_events`** — eso mantiene en pie
  la regla de que el servicio de alarmas no resuelve eventos.
- **Auditoría**: `legacy.alarm.activate` y `legacy.event.resolve`. El prefijo
  `legacy.` es lo que avisa, al leer el log, que esa identidad entró por una
  puerta sin autenticar.

## Correr

```bash
python -m gtd.legacy
```

Configuración en el mismo `.env` (ver `.env.example`, bloque `GTD_LEGACY_*`).
Sin `GTD_LEGACY_DSN` acepta los mensajes y los tira, avisando fuerte.

```bash
sudo cp deploy/cps-legacy-app.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cps-legacy-app
journalctl -u cps-legacy-app -f
```

**La unidad declara `Conflicts=broker-bridge.service`**, y no es cosmético: los
dos consumen `cliente/servidor`. Con los dos vivos, cada botón dispara dos veces
y el barrio recibe todo duplicado. `systemctl start cps-legacy-app` para el
bridge solo, y `systemctl start broker-bridge` hace el rollback igual de fácil.

## El corte

El orden importa, y cada paso es reversible solo.

1. **Ensayo con prefijo.** Levantar el puente con `GTD_LEGACY_RTDB_PREFIJO` y
   `GTD_LEGACY_PUSH=false`, con el bridge todavía vivo. Comparar lo que escribe
   contra lo que hay en los paths reales. Nadie se entera.
2. **Push a un tópico de prueba** antes de apuntarle a un barrio: una
   notificación equivocada la reciben todos los teléfonos de esa zona y no se
   puede deshacer.
3. **El corte.** `systemctl start cps-legacy-app` — la unidad declara
   `Conflicts=broker-bridge.service`, así que para el bridge sola. Rollback:
   `systemctl start broker-bridge`.
4. **Reglas RTDB a solo lectura**, para que la deriva sea imposible por
   estructura y no por disciplina. Rompe el alta de familiares desde la app
   vieja: esos vecinos pasan a llamar a CPS.
5. **Borrado único** de `InstruccionesActivacion` en los marcadores, para que la
   app no muestre la última activación del bridge como si fuera de ahora.

Falta además, y no depende de este puente:

- Flota `AlarmaV6` conectando por 8883 con credenciales de producción.
- Inventario cerrado de `CENTRALVECINAL##` — no asumir que 05 y 06 son todas.

### La service account

Vive en la Raspberry (`~/SistemaCPS/BrokerBridge/systemV3/serviceAccountKeyapp.json`),
que es de donde la toma hoy el `broker-bridge`. **No va al repo.** El puente la
lee por ruta (`GTD_LEGACY_SA_FILE`), igual que el provisioner con sus salts.
