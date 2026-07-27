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

- **PA1 — Cambios de firmware — ✅ TODOS HECHOS** (build en verde, host 52/52 con KAT):
  - #1 device_id → STA 6 bytes; #2 buffers `dev[16]`→`[20]`;
    #3 `task_mqtt` → `get_role_secret(WM_ROLE_MQTT)`; #4 **MAC del rol MQTT a STA**;
    #5 **HMAC-SHA256(SALT_MQTT, MAC)** en C puro (`hmac_sha256.c`, KAT RFC 4231),
    96 bits — **algoritmo congelado**;
    #6 **gating del arranque MQTT al reloj confiable** (evita fallo TLS
    cert-not-yet-valid en boot sin DS3231);
    #7 **guard de build** (`build_guard.h`) que rechaza los salts placeholder en
    producción — verificado que dispara.
  - **Acción operativa restante (no es código):** inyectar el `SALT_MQTT` real por
    `-D` en el build de producción (nunca commitear) y compartirlo con la CPS. El
    guard obliga: `PRODUCTION_BUILD=1` no compila con el placeholder.
- **PA2 — `SALT_MQTT` de producción:** cambiar el placeholder y compartirlo por
  canal seguro (fuera del repo).
- **PA4 — 🔴 BLOQUEANTE: el salt no reproduce el vector de verificación**
  (2026-07-27). El doc de provisioning entregado al equipo de servidor publica
  este vector: MAC `A8:42:E3:8F:CA:6C` → password `4EA453D76DD9E1C81A0D141B`.
  Con el salt disponible hoy, **ninguna de 16 variantes del algoritmo lo
  reproduce** (`deploy/diag-salt.sh`): ni key/msg invertidos, ni la MAC como
  string, ni con `\0`/`\n` al final, ni SHA-1, ni SHA-256 sin HMAC. Que fallen
  todas descarta un problema de formato: **el salt no es el que generó el vector**.
  - Hipótesis A: el salt disponible es el **placeholder** de laboratorio. Encaja
    con que la placa de prueba corre en `MODO LABORATORIO` con password fija
    (`dev-sin-secreto`), o sea que su build **no deriva nada**.
  - Hipótesis B: el vector del doc quedó **viejo**, generado con un salt anterior.
  - **Para cerrarlo** hace falta que quien compiló el build de producción confirme
    el `SALT_MQTT` inyectado por `-D` y **regenere el vector con ese salt**.
  - **Consecuencia:** no se puede dar de alta ningún panel por derivación hasta
    resolverlo — quedarían con credenciales que su firmware no puede reproducir, y
    el síntoma (`Not authorized`) aparecería recién en el campo.
  - **Interín:** registrar con la password del vector explícita
    (`PANEL_PASSWORD=…`). Si un build de producción conecta con eso, el vector es
    bueno y el problema era el salt (hipótesis A).
- **PA3 — Estación de flasheo:** confirmar que existe/estará (define D5).
