# 00 — Panorama

## Qué es el GtD

El **Gateway to Device** es el puente entre los paneles de alarma ESP32 (firmware
**AlarmaV6**) y el backend de CPS Security. Es el **único componente que habla
MQTT** del lado servidor.

```
Paneles ──MQTT/TLS──► [ GtD ] ──► Postgres ◄── Backend de App ──REST/WS/push──► App / Central
             av/<MAC>/...           (verdad + bus                (autoriza, audita)
                                     LISTEN/NOTIFY)
```

## Principios de diseño (no negociables)

1. **El GtD tiene exactamente dos caras: MQTT (paneles) y Postgres (el resto).**
   No expone ningún puerto de entrada. Quien quiere alcanzar un panel (web,
   instalador, app) **escribe en Postgres**, y el GtD lo baja a MQTT. Esto
   mantiene al GtD como un puente aburrido y a prueba de balas — el camino
   crítico de seguridad no se acopla a clientes externos.

2. **El backend de app nunca toca MQTT.** Se comunica con el GtD solo por
   Postgres (tablas + `LISTEN/NOTIFY`). La app y la central de monitoreo van por
   el backend de app, no por MQTT directo. Toda la multi-inquilinidad (¿este
   usuario es dueño de este panel?) vive en Postgres, no en ACLs de MQTT.

3. **El panel es la única autoridad de su estado.** Nadie *fija* el estado de la
   alarma; todos *piden* y el panel *confirma*. El GtD refleja lo que el panel
   reporta, nunca asume. La confirmación de un comando vuelve siempre por el
   uplink (ver [01](01-mqtt-contract.md)).

4. **Postgres es la única fuente de verdad y el bus** entre el GtD y el backend
   de app. Los dos backends nunca se hablan directo.

## Las dos tuberías

- **Uplink** (panel → base): `av/+/{status,tele,up}` → parseo/validación →
  escribe `panel_state` / `eventos`. El trigger de Postgres hace
  `NOTIFY app_panel_state` → el backend de app empuja a la app.

- **Downlink** (base → panel): `NOTIFY gtd_commands|gtd_config` → el GtD publica
  `av/<MAC>/cmd` (o `cfg` retained). La confirmación vuelve por el uplink
  (`up t:ack` / `up t:alarma` con el mismo `cid`), que marca el comando
  `confirmed`.

## Separación en dos servicios

GtD y Backend de App son **servicios separados** a propósito: el GtD es el camino
crítico (no puede caerse ni perder una alarma) y no debe reiniciarse cuando se
deploya la app. La frontera por Postgres permite deployar/escalar la app sin
tocar el ingreso de alarmas.

## Autonomía del panel (contexto del firmware)

La activación por **control RF** (llavero) es local: el panel la procesa sin
tocar el servidor y funciona con internet caído. El servidor es para control
remoto + visibilidad, **nunca** para la función núcleo de la alarma. Prioridad
absoluta del producto: autonomía.
