# Gateway to Device (GtD)

Puente entre los paneles de alarma ESP32 (firmware **AlarmaV6**) y el backend de
CPS Security. Es el **único componente que habla MQTT** del lado servidor —
*del sistema nuevo*: en el servidor todavía corre `broker-bridge` (MQTT→Firebase)
sirviendo a las centrales de la generación anterior, pendiente de apagado. Usa
otro espacio de tópicos, no colisiona. Ver [`deploy/README.md`](deploy/README.md).

> Vive por ahora dentro del repo del firmware para mantener el contrato
> sincronizado. Se separará a su propio repo más adelante.

## 📚 Documentación (contexto autosuficiente)

Todo el contexto de diseño y sincronización vive en [`docs/`](docs/README.md) —
pensado para viajar con el proyecto cuando se separe a otro repo:

- [00 — Panorama](docs/00-overview.md) · arquitectura y principios
- [01 — Contrato MQTT](docs/01-mqtt-contract.md) · **el punto de sincronización** con el firmware
- [02 — Provisioning y auth](docs/02-provisioning-auth.md) · identidad, credenciales, ACL
- [03 — Modelo de datos](docs/03-data-model.md) · esquema Postgres y qué implementa el equipo web
- [04 — Decisiones](docs/04-decisions.md) · registro de decisiones, deuda técnica, puntos abiertos

## Arquitectura (dónde encaja)

```
Paneles ──MQTT/TLS──► [ GtD ] ──► Postgres ◄── Backend de App ──REST/WS/push──► App
             av/<MAC>/...           (verdad + bus                (autoriza, audita)
                                     LISTEN/NOTIFY)
```

- El GtD tiene **exactamente dos caras**: MQTT (paneles) y Postgres (el resto).
  **No** expone puertos de entrada: quien quiere alcanzar un panel escribe en
  Postgres, y el GtD lo baja a MQTT.
- El **backend de app nunca toca MQTT**; se comunica con el GtD solo por Postgres
  (tablas + `LISTEN/NOTIFY`).
- La app y la central de monitoreo van por el **backend de app**, no por MQTT.

### Dos tuberías

- **Uplink** `av/+/{status,tele,up}` → `pipeline/uplink.py` → `repo` (escribe
  `panel_state` / `eventos`). El trigger de Postgres hace `NOTIFY app_panel_state`
  → el backend empuja a la app.
- **Downlink** `NOTIFY gtd_commands|gtd_config` → `pipeline/downlink.py` → publica
  `av/<MAC>/cmd` (o `cfg` retained). La **confirmación** vuelve por el uplink
  (`up t:ack` / `up t:alarma` con el mismo `cid`), que marca el comando `confirmed`.

## Contrato firmware↔GtD

`src/gtd/domain/` es **espejo exacto** del firmware. Fuente de verdad:

| Python | Firmware |
|---|---|
| `domain/contract.py` (slugs, canales, tipos) | `alarma_core.h`, `mqtt_parse.h`, `mqtt_config.h` |
| `domain/models.py` (campos JSON) | `mqtt_payload.c` |

Si el firmware cambia algo, se cambia acá y `tests/test_payloads.py` debe fallar
hasta reconciliar. **El contrato no puede divergir en silencio.**

## Estado actual (esqueleto)

- ✅ MQTT (uplink + downlink), parseo y validación del contrato, bucle de servicio
  con reconexión.
- 🔩 **Postgres todavía no existe.** Con `GTD_PG_DSN` vacío corre con `StubRepo`
  (in-memory + logs): sirve para probar todo el lado MQTT sin base. El downlink
  queda dormido (sin base no hay comandos que bajar).
- ⏳ **Para el equipo web:** implementar `db/repo.py::PgRepo` y
  `db/listener.py::PgListener` contra `migrations/001_init.sql`. Los pipelines no
  se tocan.

## Correr

```bash
cp .env.example .env          # completar GTD_MQTT_PASSWORD por entorno
pip install -e ".[dev]"
python -m gtd                 # arranca con StubRepo si no hay GTD_PG_DSN
pytest                        # valida el contrato
```

## Seguridad

- TLS obligatorio al broker; credencial del GtD por entorno (nunca en el repo).
- La `cfg` lleva passwords WiFi → **cifradas en reposo en Postgres**, y los logs
  **redactan** secretos (`obs/logging.py::redact`).
