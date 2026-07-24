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

    # Postgres — vacío ⇒ StubRepo (sin base todavía)
    pg_dsn: str = ""

    # Observabilidad
    log_level: str = "INFO"

    @property
    def use_postgres(self) -> bool:
        return bool(self.pg_dsn)
