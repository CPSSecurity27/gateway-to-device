# Documentación del Gateway to Device (GtD)

Contexto **autosuficiente**: cuando este proyecto se mueva a su propio repo,
seguirá teniendo todo lo necesario para entenderlo, mantenerlo y sincronizarlo
con el firmware de la alarma (AlarmaV6) sin acceso a aquel repo.

| Doc | Qué contiene |
|---|---|
| [00 — Panorama](00-overview.md) | Arquitectura del sistema, dónde encaja el GtD, principios de diseño |
| [01 — Contrato MQTT](01-mqtt-contract.md) | **El punto de sincronización.** Tópicos, envelope, todos los payloads, slugs, idempotencia. Espejo del firmware |
| [02 — Provisioning y auth](02-provisioning-auth.md) | Identidad del equipo, derivación de credenciales, alta en el broker, ACL |
| [03 — Modelo de datos](03-data-model.md) | Esquema Postgres, canales NOTIFY, qué debe implementar el equipo web |
| [04 — Registro de decisiones](04-decisions.md) | Decisiones tomadas (con fecha), deuda técnica y puntos abiertos |
| [05 — Respuestas al equipo web](05-preguntas-equipo-web.md) | Ronda 2026-08-03: esquema de `cfg`, merge por sección, `eid`, `dni`, `tsq`, LWT vs watchdog, estado de PA4 |
| [06 — Respuesta a la guía de PgRepo](06-respuesta-guia-pg.md) | Ronda 2026-08-03: qué falta del contrato de las 8 funciones para escribir `PgRepo` (8 puntos, 2 bloqueantes) — **cerrado por el 07** |
| [07 — Decisiones de la integración](07-decisiones-integracion.md) | **2026-08-04: el cierre.** Las 8 respuestas resueltas e implementadas en los dos repos; `PgRepo`/`PgListener` reales; 16 casos de integración en verde. El contrato vive en el repo web |
| [08 — Propuestas al firmware](08-propuestas-firmware.md) | Lo que solo el firmware puede arreglar: `central` en el `cfg_full`, la `cfg` retenida con secretos, la alarma sin PUBACK |

## Regla de oro de sincronización

`src/gtd/domain/` es **espejo exacto** del contrato del firmware. La fuente de
verdad vive en el repo del firmware (rutas en [01](01-mqtt-contract.md)). Si el
firmware cambia un slug o un campo, se actualiza el dominio **y**
`tests/test_payloads.py` debe fallar hasta reconciliar. El contrato no puede
divergir en silencio.
