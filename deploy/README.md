# Despliegue del GtD

Estado real del servidor (Raspberry Pi, Raspbian 13, `ServidorCPS`):

- **mosquitto corre en la misma máquina** que el GtD. Hasta ahora solo tenía el
  listener **1883 en claro y anónimo**, expuesto a internet.
- **`broker-bridge.service`** (MQTT→Firebase) también habla MQTT, contra 1883.
  Usa otro espacio de tópicos (`cliente/servidor`, `servidor/<Marcador>`), así que
  **no colisiona** con `av/<MAC>/…`. El README principal dice que el GtD es el
  único componente que habla MQTT: hoy no es cierto, conviene corregirlo.
- **Postgres no está instalado** → el GtD corre con `StubRepo`: valida, loguea, y
  **no persiste nada**. El downlink queda dormido.

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
