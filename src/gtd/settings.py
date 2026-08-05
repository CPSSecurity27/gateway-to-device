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
    # Los SALTS viven ACÁ y en ningún otro lado: quien los tiene puede calcular
    # las credenciales de toda la flota. La web nunca los ve — solo dice
    # "registrá esta MAC" encolando en gtd.provisioning_queue.
    #
    # Son TRES y no son intercambiables:
    #   salt_mqtt  equipo <-> broker.  HMAC-SHA256 sobre la MAC STA.
    #   salt_tec   usuario `admin` del portal local. djb2 sobre la MAC SoftAP.
    #   salt_cps   usuario `cps` del portal local. Mismo hash, otro salt.
    # Tienen que ser IDÉNTICOS a los del firmware (wifi_secrets.local.h) o las
    # credenciales no matchean con el equipo.
    salt_mqtt: str = ""
    salt_tec: str = ""
    salt_cps: str = ""
    # Interín para builds de laboratorio: password fija, no usa el salt.
    panel_password: str = ""
    provisioner_script: str = "deploy/provision-panel.sh"

    # Clave de cifrado de las credenciales del portal, base64 de 32 bytes. La
    # COMPARTE con el backend web, que descifra para mostrarlas en la ficha. Es
    # lo único de este bloque que la web también tiene; los salts, no.
    #   openssl rand -base64 32
    cred_key: str = ""

    # Al arrancar, revocar las credenciales de equipos que ya no existen (un
    # alta que falló a mitad de camino las deja registradas). Apagable por si
    # hay que mirar antes de que toque nada.
    barrer_huerfanos: bool = True

    # DESARROLLO: no invocar el script ni tocar Mosquitto — anotar y decir que
    # salió bien. Sin esto no se puede probar el alta de fábrica fuera de la
    # Raspberry, porque `provision-panel.sh` necesita bash, mosquitto_passwd y
    # escritura en /etc/mosquitto.
    #
    # La derivación de las credenciales del portal SÍ es real: es cómputo puro y
    # es la parte que puede salir mal en silencio. Lo único que se saltea es el
    # registro en el broker.
    #
    # Que sea explícito y no autodetectado: "estoy en Windows, debe ser
    # desarrollo" es la clase de inferencia que un día no registra nada en
    # producción y nadie se entera.
    registrador_falso: bool = False

    # Observabilidad
    log_level: str = "INFO"

    @property
    def use_postgres(self) -> bool:
        return bool(self.pg_dsn)
