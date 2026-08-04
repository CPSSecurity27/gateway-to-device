# Despliegue del GtD

Estado real del servidor (Raspberry Pi, Raspbian 13, `ServidorCPS`):

- **mosquitto corre en la misma máquina** que el GtD. Hasta ahora solo tenía el
  listener **1883 en claro y anónimo**, expuesto a internet.
- **`broker-bridge.service`** (MQTT→Firebase) también habla MQTT, contra 1883.
  Usa otro espacio de tópicos (`cliente/servidor`, `servidor/<Marcador>`), así que
  **no colisiona** con `av/<MAC>/…`. El README principal dice que el GtD es el
  único componente que habla MQTT: hoy no es cierto, conviene corregirlo.
- **Postgres 17.10 SÍ está instalado y corriendo** (verificado 2026-08-04),
  escuchando solo en `127.0.0.1:5432`. Lo que falta es el `GTD_PG_DSN` en el
  `.env`: mientras esté vacío el GtD corre con `StubRepo` —valida, loguea y **no
  persiste nada**— y el downlink queda dormido. La base que hay es
  `cps_security_monitoring`, con el esquema v2 congelado en la migración 4 de 16
  y sin datos que valgan; al desplegar se rehace con el nombre `cpssecurityarg`.

## Qué hace el despliegue

`install-root.sh` agrega el listener **8883 con TLS y autenticación** para el GtD,
sin tocar el 1883. Los paneles siguen entrando en claro hasta que el firmware
provisione credenciales (ver [`../docs/02-provisioning-auth.md`](../docs/02-provisioning-auth.md)).

Separación por listener con `per_listener_settings true`:

| | 1883 | 8883 |
|---|---|---|
| TLS | no | sí (Let's Encrypt) |
| auth | anónimo | usuario `gateway` |
| ACL | ninguna | [`gtd.acl`](gtd.acl) |
| quién | paneles, broker-bridge | GtD |

## Correr

```bash
ssh ServidorCPS
cd ~/SistemaCPS/gateway-to-device
git pull
sudo bash deploy/install-root.sh
```

Es **idempotente**: se puede repetir sin duplicar config ni rotar la password.

Antes de dar nada por bueno, el script verifica que **1883 siga aceptando
anónimos** y que **`broker-bridge` siga vivo**. Si algo de eso falla, revierte la
configuración de mosquitto sola y aborta. El backup queda en `/root/gtd-backup-*`.

## Después

```bash
journalctl -u gateway-to-device -f
```

Un panel conectándose se ve así:

```
[+] panel ONLINE  mac=AA:BB:CC:DD:EE:FF modo=ACTIVE_240 fw=6.0.0 (antes=?)
[-] panel OFFLINE mac=AA:BB:CC:DD:EE:FF causa=lwt (antes=online)
```

Solo se loguea el **cambio** de estado, no cada mensaje retenido.

## El sistema viejo — PENDIENTE DE APAGADO, no tocar todavía

Conviven dos generaciones en el mismo broker. Esto está documentado porque la
decisión de apagar lo viejo ya se tomó, pero **no se puede ejecutar todavía**.

**Qué es lo viejo:**

- **Centrales `CENTRALVECINAL##`** — equipos en producción, publicando sobre 1883
  en claro y sin credenciales (verificado el 2026-07-24: 05 y 06 reportando en
  vivo). Tópicos `CENTRALVECINAL##/servidor`, `cliente/servidor`,
  `servidor/<Marcador>`. **No hablan `av/`**: el GtD ni siquiera las parsea.
- **`broker-bridge.service`** — traduce ese MQTT a push de Firebase (FCM). Es
  **lo único** que hoy le avisa a la app que se disparó una alarma.

**Por qué no se apaga todavía.** Apagarlo hoy deja a los usuarios de esas
centrales sin aviso de alarma, y el GtD no los cubre: es otra generación de
equipo, y el push en el diseño nuevo lo hace el backend de app leyendo Postgres,
que todavía no existe. En un sistema de alarmas eso no falla ruidosamente — falla
el día que alguien necesita el aviso.

**Checklist para poder apagarlo** (cuando estén los cuatro, se retira sin huecos):

- [ ] Postgres instalado y `PgRepo`/`PgListener` implementados.
- [ ] Backend de app leyendo `LISTEN/NOTIFY` y mandando push.
- [ ] Flota `AlarmaV6` flasheada con el `SALT_MQTT` de producción y conectando
      por 8883 con credenciales.
- [ ] Cada `CENTRALVECINAL##` reemplazada o migrada — inventario cerrado, no
      asumir que 05 y 06 son todas.

**Cómo apagarlo, llegado el momento:**

```bash
sudo systemctl disable --now broker-bridge
# y en /etc/mosquitto/conf.d/default.conf, quitar el bloque `listener 1883`
sudo systemctl restart mosquitto
```

Mientras tanto, lo nuevo sí está protegido: [`secure-av-namespace.sh`](secure-av-namespace.sh)
le pone al 1883 una ACL que **niega `av/#`** y deja el resto abierto. El broker es
un solo bus, así que sin eso cualquiera publicaría en `av/<MAC>/cmd` desde el
puerto en claro y un panel en 8883 obedecería, salteándose la ACL autenticada.
Las centrales no se ven afectadas: nunca usan `av/`.

## Otros pendientes

1. **Postgres.** Sin él no hay persistencia ni downlink. Falta `PgRepo` /
   `PgListener` contra `migrations/001_init.sql`.
2. **Paneles en 8883.** Requiere el `SALT_MQTT` real en el build de producción y
   la ACL por panel (hoy comentada en [`gtd.acl`](gtd.acl)).

## El provisioner — alta y baja de credenciales

`cps-provisioner.service` es un **segundo proceso**, aparte del GtD:

```bash
sudo cp deploy/cps-provisioner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cps-provisioner
journalctl -u cps-provisioner -f
```

**Por qué no es una tarea más del GtD.** `gateway-to-device.service` está
endurecido a propósito (`NoNewPrivileges=yes`, `ProtectSystem=strict`,
`/etc` de solo lectura) porque recibe payloads de cada panel por MQTT.
Registrar en el broker necesita justo lo contrario: escribir
`/etc/mosquitto/gtd.passwd` y recargar mosquitto. Meterlo adentro sería desarmar
ese encierro en el proceso más expuesto del sistema.

El provisioner, en cambio, **no habla MQTT**: su única entrada son filas de
`gtd.provisioning_queue` en la base local. Corre como root, pero con una
superficie de ataque mucho más chica.

### Configuración

Las dos claves nuevas van en el mismo `.env`:

```
GTD_SALT_MQTT=          # el secreto de derivación
GTD_PANEL_PASSWORD=     # alternativa para builds de laboratorio
```

**El `SALT_MQTT` vive acá y en ningún otro lado.** Quien lo tiene puede calcular
la credencial de cualquier panel de la flota, así que **nunca** va en el `.env`
de la web. El script lo valida contra un vector de verificación conocido antes
de registrar nada: con un salt equivocado aborta sin tocar el broker, en vez de
cargar credenciales que fallan recién cuando el panel intenta conectar.

Sin ninguna de las dos, el script pedirá el salt por consola y no habrá nadie
para tipearlo: el provisioner avisa al arrancar.

### Qué hace

Drena la cola invocando `provision-panel.sh` con `--no-reload --no-probe`, y
**recarga mosquitto una sola vez al final de la tanda**. Los dos flags importan
con volumen: 200 equipos serían 200 reloads, y la prueba de verificación
publicaría 200 `status` falsos que el GtD tomaría como conexiones reales,
escribiéndoles `first_connection_at` con los paneles todavía en la caja.

Barre cada 60 s además del `NOTIFY`: una notificación emitida mientras el
proceso estaba caído no vuelve nunca.
