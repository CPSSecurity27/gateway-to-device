"""Configuración por entorno (prefijo GTD_). Ver .env.example."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GTD_", env_file=".env", extra="ignore"
    )

    # MQTT
    mqtt_host: str = "cpssecurity.com.ar"
    mqtt_port: int = 8883
    mqtt_username: str = "gateway"
    mqtt_password: str = ""          # secreto — por entorno
    mqtt_ca_file: str = ""           # vacío = bundle del sistema
    mqtt_client_id: str = "gtd-1"

    # Presencia — ver pipeline/presencia.py
    # Silencio que marca a un panel como caído. Generoso a propósito: sobre enlaces
    # satelitales (Starlink) se midieron cortes de ~50 s que NO son una falla.
    presence_timeout_s: int = 180
    # Silencio previo mínimo para leer un `status` repetido como una reconexión.
    # Por encima de la cadencia de telemetría (30 s) y del mayor hueco medido (63 s).
    reconnect_gap_s: int = 60

    # Postgres — vacío ⇒ StubRepo (sin base). Conexión DIRECTA, sin pgbouncer:
    # si algún día hay pooler, el listener necesita un DSN directo aparte.
    pg_dsn: str = ""

    # Spool del canal `up`: eventos que no pudieron entrar a la base (el PUBACK
    # ya salió, no existen en ningún otro lado). Relativo al working dir.
    spool_path: str = "var/spool-up.jsonl"

    # ── Provisioner (proceso aparte: python -m gtd.provisioner) ──────────
    # El SALT vive ACÁ y en ningún otro lado: quien lo tiene puede calcular la
    # credencial de cualquier panel de la flota. La web nunca lo ve — solo dice
    # "registrá esta MAC" encolando en gtd.provisioning_queue.
    salt_mqtt: str = ""
    # Interín para builds de laboratorio: password fija, no usa el salt.
    panel_password: str = ""
    provisioner_script: str = "deploy/provision-panel.sh"

    # Observabilidad
    log_level: str = "INFO"

    @property
    def use_postgres(self) -> bool:
        return bool(self.pg_dsn)
