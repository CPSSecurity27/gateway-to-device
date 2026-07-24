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
usuario  = "AV-" + hex_mayus(MAC_STA[0..5])         # ✅ implementado

# password — INTERÍN actual (implementado):
password = "SCPS-" + hex_mayus( djb2(SALT_MQTT, MAC_STA[0..5]) & 0xFFFFFF )
           # djb2-xor sobre SALT_MQTT y luego los 6 bytes; 24 bits

# password — OBJETIVO (pendiente #5, congelar antes de armar la herramienta CPS):
password = "SCPS-" + hex_mayus( HMAC-SHA256(SALT_MQTT, MAC_STA[0..5]) truncado )
```
Ambos usan la **MAC STA** y `SALT_MQTT`. La herramienta de provisioning debe
replicar el algoritmo **vigente** byte a byte (hoy djb2; migrará a HMAC).

- El **panel calcula su propia credencial** de su MAC en cada arranque — cero
  provisioning del lado del equipo, mismo firmware para toda la flota.
- La herramienta de provisioning (equipo web) calcula **la misma** password de la
  MAC y la registra en el broker. Como ambos derivan por cálculo, **no se
  transporta ni guarda ninguna password manualmente**.
- `SALT_MQTT` **no se imprime ni se commitea**. Se comparte por canal seguro entre
  firmware y provisioning. Debe coincidir byte a byte. Default actual en firmware
  es un placeholder a cambiar antes de producción.

> ✅ **Estado firmware:** el fix ya está implementado (build en verde) — usuario y
> password usan la **MAC STA** y la password deriva de **`SALT_MQTT`** (desacoplada
> del salt de la etiqueta WiFi). Interín djb2/24-bit.
> ⚠️ **Pendiente antes de provisionar el broker:** congelar #5 (HMAC) y poner el
> `SALT_MQTT` real (hoy placeholder). Ver [04](04-decisions.md).

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
