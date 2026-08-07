# 02 — Provisioning y autenticación

Cómo cada panel obtiene su identidad y credenciales, y cómo se da de alta en el
broker. Contexto para el equipo de servidor y el equipo web.

## Identidad del equipo

- Usuario MQTT = client_id = **`AV-<MAC STA, 6 bytes, hex mayúsculas>`**
  (ej. `AV-3C71BF9A2D01`).
- La **MAC STA es la MAC base del eFuse**: `esp_efuse_mac_get_default()` en el
  firmware, y `esptool read_mac` en la estación de flasheo la devuelven tal cual,
  sin transformación. Es única e inmutable por unidad física.
- ✅ El firmware ya usa **6 bytes** (`AV-<12 hex>`). (Antes usaba 3 → colisión
  ~50% a ~4.800 equipos.) Ver [04](04-decisions.md).
- La MAC del **AP WiFi** es distinta (SoftAP = base+1) y **no se usa para MQTT**.

## Derivación de credenciales

```
usuario  = "AV-" + hex_mayus(MAC_STA[0..5])            # ✅ implementado

# password — VIGENTE (✅ implementado; algoritmo CONGELADO para la CPS):
h        = HMAC-SHA256(key = SALT_MQTT, msg = MAC_STA[0..5])   # RFC 2104 + SHA-256
password = hex_mayus(h[0..11])                         # primeros 12 bytes = 96 bits
```
- `key` = los bytes del string `SALT_MQTT` (**sin** el NUL final; `strlen`).
- `msg` = los **6 bytes crudos** de la MAC STA (los mismos del usuario).
- hex en **MAYÚSCULAS**, 2 chars por byte → **24 chars exactos, sin prefijo**.

> ✅ **Verificado contra el firmware (2026-07-27).** Este documento decía antes
> `password = "SCPS-" + hex`, y era **incorrecto**. La fuente
> (`components/wifi_manager/wifi_manager.c`, `wifi_manager_get_role_secret`) emite
> los 12 bytes como `"%02X"` y nada más:
>
> ```c
> cps_hmac_sha256(SECURITY_SALT_MQTT, strlen(...), mac /*6 bytes STA*/, 6, h);
> for (int i = 0; i < 12; i++) snprintf(out + i*2, 3, "%02X", h[i]);
> ```
>
> **24 chars, sin prefijo.** Coincide con el doc entregado al equipo de servidor.

### El broker se REINICIA, nunca se recarga

Después de escribir una credencial hay que hacerle saber al broker. Va
`systemctl restart mosquitto`, **no `reload`**.

El SIGHUP del reload le pide a mosquitto rearmar su contexto TLS en caliente y
lo deja a medias: el listener 8883 sigue aceptando la conexión TCP pero no
completa el handshake. Los paneles quedan reintentando cada 30 s con
`MBEDTLS_ERR_SSL_FATAL_ALERT_MESSAGE` (-0x7280) **justo después de validar el
certificado del servidor**, y en el log del broker se ve así:

```
1786025551: New client connected as AV-A842E38FCA6C      <- antes del reload
1786025604: Client AV-A842E38FCA6C disconnected
1786026110: New connection from 148.222.217.253 on port 8883   <- y desde acá
1786026147: New connection from 148.222.217.253 on port 8883      solo esto,
1786026184: New connection from 148.222.217.253 on port 8883      cada 37 s
```

Nunca más un `New client connected`, y **ni un solo error**. Eso es lo que lo
hace peligroso: el reload no avisa que falló.

Pasó en producción el 2026-08-06 — fabricar UN equipo dejó a la flota entera
afuera durante horas. El restart corta las conexiones vivas, pero dura segundos
y los paneles reconectan solos con su backoff; el GtD también.

Sigue siendo **uno por tanda**: fabricar diez equipos reinicia una vez
(`servicio.py`, fase 2).

### Vector de verificación

Con el `SALT_MQTT` **de producción**:

| Campo | Valor |
|---|---|
| MAC | `A8:42:E3:8F:CA:6C` |
| usuario | `AV-A842E38FCA6C` |
| password | `4EA453D76DD9E1C81A0D141B` |

`deploy/provision-panel.sh` valida el salt contra este vector **antes** de registrar
nada: un salt equivocado produce credenciales que parecen válidas y fallan recién
cuando el panel intenta conectar.

El salt se acepta por `MQTT_DERIV_SALT` (nombre del doc de servidor) o `SALT_MQTT`
(nombre del firmware) — es el mismo secreto.
- Implementación de referencia: `components/wifi_manager/hmac_sha256.c` (C puro,
  verificado con el KAT de RFC 4231). La CPS puede reusar ese archivo o cualquier
  HMAC-SHA256 estándar — el KAT garantiza que coinciden **byte a byte**.

- El **panel calcula su propia credencial** de su MAC en cada arranque — cero
  provisioning del lado del equipo, mismo firmware para toda la flota.
- La herramienta de provisioning (equipo web) calcula **la misma** password de la
  MAC y la registra en el broker. Como ambos derivan por cálculo, **no se
  transporta ni guarda ninguna password manualmente**.
- `SALT_MQTT` **no se imprime ni se commitea**. Se comparte por canal seguro entre
  firmware y provisioning. Debe coincidir byte a byte. Default actual en firmware
  es un placeholder a cambiar antes de producción.

> ✅ **Estado firmware:** implementado (build en verde, host 52/52 con KAT).
> Usuario y password usan la **MAC STA**; password = **HMAC-SHA256(`SALT_MQTT`,
> MAC)**, 96 bits, desacoplada de la etiqueta WiFi. **Algoritmo congelado.** Un
> guard de build (`build_guard.h`) impide compilar producción con el `SALT_MQTT`
> placeholder.
> ⚠️ **Acción operativa antes de provisionar:** inyectar el `SALT_MQTT` real por
> `-D` en el build de producción (nunca commitear) y compartirlo con la CPS.

## Flujo de provisioning (estación de flasheo)

```
ESTACIÓN (USB, por equipo) — el equipo NO se conecta al broker todavía:
  1. flashear firmware (imagen única para toda la flota)
  2. esptool read_mac → MAC base (= MAC STA)
  3. equipo web da de alta el equipo: inventario de fábrica (mac, datos)
     + registra la credencial MQTT (usuario+password derivados) + ACL
CAMPO (después):
  4. el equipo arranca, se conecta → el broker YA lo conoce → auth OK
```

Clave: el registro se hace **antes** de que el equipo se conecte. El broker
**nunca auto-aprende** (`allow_anonymous false`): la password se deduce de la MAC
y se registra proactivamente. Cuando el equipo se conecta, ya es un invitado en la
lista, no un desconocido.

## ACL en el broker

Los **paneles** no necesitan entradas por equipo: un solo patrón `%u` (usuario
autenticado) cubre toda la flota.

```
# Paneles (un bloque para todos)
pattern write av/%u/status
pattern write av/%u/tele
pattern write av/%u/up
pattern read  av/%u/cfg
pattern read  av/%u/cmd

# GtD (único principal de servidor sobre MQTT)
user gateway
topic read  av/+/status
topic read  av/+/tele
topic read  av/+/up
topic write av/+/cfg
topic write av/+/cmd
topic write av/all/cmd
```

Se usa `%u` (usuario, autenticado por password), **no** `%c` (client_id,
falsificable). Un panel comprometido queda confinado a su propio subárbol.

> La app y la central **no** tienen usuario MQTT: van por el backend de app.

## Auth del broker: objetivo vs interín

- **Objetivo (con Postgres):** Mosquitto autentica contra Postgres
  (`mosquitto-go-auth`). El equipo web hace un INSERT del equipo → el panel ya
  puede conectarse. Fuente única, sin archivos que sincronizar.
- **Interín:** `password_file` estático + un script temporal que corre
  `mosquitto_passwd` por equipo. Muleta hasta la integración web/Postgres.

## Server (estado y pendientes)

- Broker Mosquitto en Raspberry Pi, dominio `cpssecurity.com.ar`, cert Let's
  Encrypt (encadena a ISRG Root X1, presente en el bundle del ESP32 → valida).
- Listener TLS 8883 (`allow_anonymous false`, `password_file`/DB). 1883 restringido
  a localhost (bridge). Rate-limiting de auth vía **fail2ban** (Mosquitto no tiene
  built-in) — obligatorio mientras la password sea de baja entropía.
