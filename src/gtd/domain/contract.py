"""Contrato MQTT — ESPEJO EXACTO del firmware.

Fuente de verdad (repo del firmware AlarmaV6):
  - components/mqtt_av/mqtt_payload.h/.c  → payloads D→S (subida)
  - components/mqtt_av/mqtt_parse.h/.c    → payloads S→D (cfg/cmd, bajada)
  - components/alarma_core/alarma_core.h  → slugs de modo y origen

REGLA: si el firmware agrega un campo o cambia un slug, se cambia ACÁ y el test
`tests/test_payloads.py` debe fallar hasta reconciliar. El contrato no puede
divergir en silencio (ver README §Contrato).

Convención de TODO mensaje D→S: envelope {"v", "ts", "tsq"}.
"""

from __future__ import annotations

import re
from enum import Enum

# Versión de esquema — MQTT_SCHEMA_V en mqtt_config.h
SCHEMA_V = 1

# Raíz de tópicos — MQTT_TOPIC_ROOT en mqtt_config.h
TOPIC_ROOT = "av"

# Prefijo de identidad — MQTT_DEVICE_ID_PREFIX (usuario = client_id = AV-<MAC>)
DEVICE_ID_PREFIX = "AV-"

# Buffer de ENTRADA del panel — MQTT_IN_PAYLOAD_MAX en mqtt_config.h. Un payload
# más grande es un no-op SILENCIOSO del firmware (ni ack de error): la guarda
# vive del lado servidor porque del otro lado no hay nadie que avise.
MQTT_IN_PAYLOAD_MAX = 1024

# 12 hex MAYÚSCULAS, sin separadores — el formato canónico de device.mac en la
# base. La misma regla que chk_device_mac_format del esquema web.
_MAC_RE = re.compile(r"^[0-9A-F]{12}$")


def mac_from_device_id(device_id: str) -> str | None:
    """'AV-A842E38FCA6C' → 'A842E38FCA6C'. None si no es un id válido del firmware.

    Estricto a propósito: el firmware SIEMPRE emite AV- + 12 hex mayúsculas
    (usuario = client_id = <id> del tópico). Cualquier otra cosa en el tópico
    es un publicador ajeno, no un panel.
    """
    if not device_id.startswith(DEVICE_ID_PREFIX):
        return None
    mac = device_id[len(DEVICE_ID_PREFIX):]
    return mac if _MAC_RE.match(mac) else None


def device_id_from_mac(mac: str) -> str:
    """'A842E38FCA6C' → 'AV-A842E38FCA6C'. La ÚNICA forma de armar la identidad
    MQTT desde la MAC: si esto se hace a mano en otro lado, el bug del doc 06 §1
    (publicar en av/<mac-sin-prefijo>/cmd, que el broker acepta y tira al vacío)
    vuelve.
    """
    return f"{DEVICE_ID_PREFIX}{mac}"


class Channel(str, Enum):
    """Sub-tópico bajo av/<id>/ — dirección fija por canal (mqtt_design §4)."""
    STATUS = "status"   # D→S retained: presencia + LWT + aviso de sueño
    TELE = "tele"       # D→S retained: snapshot de telemetría
    UP = "up"           # D→S stream: discriminado por "t"
    CFG = "cfg"         # S→D retained: estado deseado (cfg_v única)
    CMD = "cmd"         # S→D stream: órdenes con cid


class UpType(str, Enum):
    """Discriminador "t" del canal up — mqtt_payload.c y task_mqtt.c."""
    ALARMA = "alarma"
    ACK = "ack"
    SCAN = "scan"
    OTA = "ota"
    CFG_FULL = "cfg_full"   # el panel espeja su config completa al conectar
    RF_RX = "rf_rx"                  # RF2: código captado en modo monitor (alta de controles)
    RF_RX_END = "rf_rx_end"          # RF2: fin del modo monitor (cmd / timeout / ventana)
    AUDIT = "audit"                  # RF6-2: checksums FNV-1a por cliente, en lotes
    AUDIT_DETALLE = "audit_detalle"  # RF6-3: códigos exactos de DNIs consultados


class AlarmaMode(str, Enum):
    """Slugs del catálogo — alarma_core.h (los números JAMÁS viajan)."""
    OFF = "off"
    SUSPICIOUS = "suspicious"
    ALERT = "alert"
    EMERGENCY = "emergency"
    FIRE = "fire"
    MEDICAL = "medical"
    SILENT = "silent"
    PANIC = "panic"


class AlarmaOrigin(str, Enum):
    """Origen del comando de alarma — alarma_origin_slug() en alarma_core."""
    RF = "rf"          # control remoto (llavero) — activación local autónoma
    MQTT = "mqtt"      # cmd t:alarma del servidor
    AUTO = "auto"      # interno: auto-off
    PORTAL = "portal"  # portal TEC/CPS local


class CmdType(str, Enum):
    """Tipos de cmd S→D — mqtt_parse.h (mqtt_cmd_type_t).

    El GtD normalmente solo EMITE estos (los arma el backend de app en Postgres);
    se enumeran completos para validar lo que se publica.
    """
    ESTADO = "estado"
    RESTART = "restart"
    ALARMA = "alarma"
    SCAN = "scan"
    TEST = "test"
    OTA = "ota"
    FACTORY = "factory"
    RF = "rf"
    REFRESH = "refresh"
    HORA = "hora"
    I2C_SCAN = "i2c_scan"
    RED = "red"
    CAL = "cal"


# tsq — rtc_time_quality_t (calidad de la hora que viaja en cada mensaje).
# El significado exacto vive en rtc_types.h del firmware; acá se guarda tal cual.
