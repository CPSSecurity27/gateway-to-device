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

## Pendientes que este despliegue NO resuelve

1. **Postgres.** Sin él no hay persistencia ni downlink. Falta `PgRepo` /
   `PgListener` contra `migrations/001_init.sql`.
2. **Paneles en 8883.** Requiere el `SALT_MQTT` real en el build de producción y
   la ACL por panel (hoy comentada en [`gtd.acl`](gtd.acl)).
3. **1883 abierto a internet** en `0.0.0.0`, anónimo. Es previo al GtD, pero
   cualquiera puede publicar en los tópicos de los paneles. Cerrarlo a localhost
   o migrar todo a 8883 en cuanto los paneles tengan credenciales.
