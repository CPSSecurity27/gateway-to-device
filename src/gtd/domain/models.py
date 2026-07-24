"""Modelos de payload (pydantic) — validación + extracción.

Criterio de diseño:
  - Se VALIDAN los discriminadores (v, t, estado) y se EXTRAEN los campos que el
    GtD indexa (mode, cfg_v, online, eid, cid...).
  - `model_config = extra="allow"`: el contrato del firmware puede sumar campos;
    no queremos rechazar un panel por eso. El documento CRUDO completo se guarda
    igual en Postgres (columna JSONB), así nunca se pierde información.

Nombres de campo = exactos del firmware (mqtt_payload.c). No traducir.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contract import SCHEMA_V, AlarmaMode, AlarmaOrigin, UpType


class _Envelope(BaseModel):
    """Campos comunes a todo mensaje D→S."""
    model_config = ConfigDict(extra="allow")

    v: int = Field(..., description="MQTT_SCHEMA_V")
    ts: int | None = Field(None, description="unix UTC del panel")
    tsq: int | None = Field(None, description="rtc_time_quality_t")


# ── status (retained) ───────────────────────────────────────────────
class StatusMsg(_Envelope):
    # "online" | "durmiendo" | "offline"(LWT)
    estado: str
    fw: str | None = None
    modo: str | None = None          # modo de energía (status online)
    despierta: int | None = None     # unix hasta cuándo duerme (status sleep)
    causa: str | None = None         # razón de sueño / "lwt"

    @property
    def online(self) -> bool:
        return self.estado == "online"


# ── tele (retained snapshot) ────────────────────────────────────────
class TeleMsg(_Envelope):
    energia: dict[str, Any] | None = None   # {modo,vbat,vpanel,vfuente}
    ota: dict[str, Any] | None = None       # {fw,estado,ultimo}
    alarma: dict[str, Any] | None = None    # {mode,act,redisp,...}
    cfg_v: int | None = None
    rf_gen: int | None = None

    @property
    def alarma_mode(self) -> str | None:
        return (self.alarma or {}).get("mode")

    @property
    def modo_energia(self) -> str | None:
        return (self.energia or {}).get("modo")


# ── up (stream, discriminado por "t") ───────────────────────────────
class UpAlarma(_Envelope):
    t: str = UpType.ALARMA.value
    eid: str                          # <boot_id>-<seq> — idempotencia
    mode: AlarmaMode
    prev: AlarmaMode
    origin: AlarmaOrigin
    dni: int | None = None            # solo origen RF
    codigos: int | None = None        # solo origen RF
    cid: str | None = None            # solo origen MQTT — correlación con commands
    rol: str | None = None            # solo origen PORTAL


class UpAck(_Envelope):
    t: str = UpType.ACK.value
    cid: str | None = None            # ack de cmd
    cfg_v: int | None = None          # ack de cfg
    res: str | None = None            # "ok" | "error"
    det: str | None = None


class UpCfgFull(_Envelope):
    """Espejo de la config completa del panel. Lo publica al conectar.

    OJO: `redes[].psw` son las passwords de WiFi en claro. Este documento NO se
    loguea nunca entero — si hiciera falta, pasarlo por `obs.logging.redact`.
    """
    t: str = UpType.CFG_FULL.value
    cfg_v: int                              # arbitra la versión: no pisar con una vieja
    redes: list[dict[str, Any]] = Field(default_factory=list)   # {ssid,psw,prio,bl_perm}
    modulos: dict[str, Any] | None = None   # {ds3231,eeprom,supervisor,rf,eeprom_slot}
    hora: dict[str, Any] | None = None      # {tz_offset_s}
    tiempos: dict[str, Any] | None = None   # {send_tele_s}
    mante: dict[str, Any] | None = None     # {on}
    alarma: dict[str, Any] | None = None    # {autooff:{<modo>:seg}}
    cal: dict[str, Any] | None = None       # {bat,panel,fuente:{m,b}}
    red_avanzada: dict[str, Any] | None = None   # {roam_rssi,roam_delta,roam_cooldown_s}
    rf: dict[str, Any] | None = None        # {total_codigos,gen}
    id: dict[str, Any] | None = None        # {dev,fw}

    @property
    def fw(self) -> str | None:
        return (self.id or {}).get("fw")

    @property
    def rf_gen(self) -> int | None:
        return (self.rf or {}).get("gen")


class UpScan(_Envelope):
    t: str = UpType.SCAN.value
    redes: list[dict[str, Any]] = Field(default_factory=list)


class UpOta(_Envelope):
    t: str = UpType.OTA.value
    estado: int | None = None
    resultado: int | None = None
    fw: str | None = None


def is_schema_ok(doc: dict[str, Any]) -> bool:
    """Chequeo barato antes de validar: v presente y compatible."""
    return doc.get("v") == SCHEMA_V
