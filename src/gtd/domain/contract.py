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

from enum import Enum

# Versión de esquema — MQTT_SCHEMA_V en mqtt_config.h
SCHEMA_V = 1

# Raíz de tópicos — MQTT_TOPIC_ROOT en mqtt_config.h
TOPIC_ROOT = "av"

# Prefijo de identidad — MQTT_DEVICE_ID_PREFIX (usuario = client_id = AV-<MAC>)
DEVICE_ID_PREFIX = "AV-"


class Channel(str, Enum):
    """Sub-tópico bajo av/<id>/ — dirección fija por canal (mqtt_design §4)."""
    STATUS = "status"   # D→S retained: presencia + LWT + aviso de sueño
    TELE = "tele"       # D→S retained: snapshot de telemetría
    UP = "up"           # D→S stream: discriminado por "t"
    CFG = "cfg"         # S→D retained: estado deseado (cfg_v única)
    CMD = "cmd"         # S→D stream: órdenes con cid


class UpType(str, Enum):
    """Discriminador "t" del canal up — mqtt_payload.c."""
    ALARMA = "alarma"
    ACK = "ack"
    SCAN = "scan"
    OTA = "ota"
    CFG_FULL = "cfg_full"   # el panel espeja su config completa al conectar


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
