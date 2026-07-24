# 04 — Registro de decisiones y puntos abiertos

Decisiones tomadas en el diseño (2026-07-24). Sirve para no re-litigar y para que
un repo separado tenga el "por qué".

## Decisiones cerradas

| # | Decisión | Motivo |
|---|---|---|
| D1 | Identidad = `AV-<MAC STA 6 bytes>` (usuario = client_id) | MAC STA = base del eFuse, leíble tal cual por firmware y esptool |
| D2 | MAC canónica = **STA**; el AP sigue en SoftAP sin tocar | evita que la herramienta de provisioning reconstruya el +1 de la SoftAP |
| D3 | Password MQTT vía `SALT_MQTT`, **desacoplada** del salt de la etiqueta WiFi | hoy son la misma (bug); el secreto de máquina ≠ secreto humano |
| D4 | Entropía: **HMAC-SHA256(SALT_MQTT, MAC)** truncado (no djb2/24-bit) | mbedTLS ya está por TLS; elimina fuerza-bruta online de 24 bits |
| D5 | Provisioning: `esptool read_mac` en la estación → alta en inventario web → registro en broker (antes de conectar) | cero pasos en campo; el broker no auto-aprende |
| D6 | Auth broker objetivo: **Postgres-backed** (mosquitto-go-auth); interín password_file + script | INSERT del web = equipo provisionado, fuente única |
| D7 | ACL paneles con patrón `%u` (no `%c`) | un bloque cubre la flota; client_id es falsificable |
| D8 | **Postgres reemplaza Firebase** como fuente de verdad + bus | consistencia; LISTEN/NOTIFY como pub/sub interno |
| D9 | **GtD = único principal MQTT**; dos caras (MQTT + Postgres), sin puerto de entrada | puente robusto, no acoplado a clientes externos |
| D10 | **App y central por el backend de app, NO por MQTT** | multi-inquilinidad en Postgres (SQL), no en ACL; mejor para móvil (push, batería, auditoría) |
| D11 | GtD y backend de app **servicios separados**, se hablan solo por Postgres | aislar el camino crítico de seguridad de los deploys de la app |
| D12 | Gatear el primer connect MQTT a **reloj confiable** (evita fallo TLS cert-not-yet-valid) | aprobado, implementar junto con el resto del firmware |

## Deuda técnica (con dueño/fecha pendiente)

- **DT1 — Password aleatoria por equipo.** La derivación MAC+SALT es determinística:
  si `SALT_MQTT` se filtra, se calcula la password de toda la flota (la MAC no es
  secreta). Objetivo real: password aleatoria en NVS (la misma estación de flasheo
  la escribe → aísla la flota) + Flash Encryption + Secure Boot (NVS encryption
  sola no alcanza). **Fasable:** (1) random por equipo aísla la flota; (2) flash
  encryption protege el equipo físico. Falta ticket con dueño y fecha.
- **DT2 — Cifrado en reposo de `panel_config`** (passwords WiFi). Lo define el
  equipo web al implementar `PgRepo`.

## Puntos abiertos

- **PA1 — Cambios de firmware:**
  - ✅ **Hecho** (build en verde, host 46/46): device_id → STA 6 bytes;
    `task_mqtt` → `get_role_secret(WM_ROLE_MQTT)`; **MAC del rol MQTT parametrizada
    a STA** (device_hash_salted); buffers `dev[16]`→`[20]`; comentarios de doc.
  - ⏳ **Pendiente:** #5 entropía → HMAC (congelar antes de la herramienta CPS);
    #6 gating de reloj (D12); #7 `SALT_MQTT` real (hoy placeholder).
  - **Bloqueante:** no provisionar el broker hasta congelar #5 y poner el salt real.
- **PA2 — `SALT_MQTT` de producción:** cambiar el placeholder y compartirlo por
  canal seguro (fuera del repo).
- **PA3 — Estación de flasheo:** confirmar que existe/estará (define D5).
