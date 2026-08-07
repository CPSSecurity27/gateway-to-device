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

    # El PROVISIONER se conecta con OTRO usuario, y no es un detalle de higiene.
    #
    # `pg_dsn` es el del GtD, que corre como `cps_alarms`: el proceso que recibe
    # payloads de cada panel y por eso vive encerrado. A ese rol el diseño le
    # NIEGA la cola de provisioning a propósito — quien puede confirmar un alta
    # puede registrar credenciales en el broker.
    #
    # El rol `cps_provisioner` existía y tenía sus GRANTs desde el primer día,
    # pero el código nunca lo usó: el provisioner tomaba `pg_dsn` y chocaba con
    # "permission denied for function fetch_pending_provisioning" (visto en
    # producción el 2026-08-06). Vacío cae a `pg_dsn`, que es lo que hacía antes.
    provisioner_dsn: str = ""

    @property
    def dsn_del_provisioner(self) -> str:
        return self.provisioner_dsn or self.pg_dsn

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

    # ── Puente con la app VIEJA (proceso aparte: python -m gtd.legacy) ───
    # TEMPORAL: se va con la app vieja, junto con la migración LegacyAppBridge.
    #
    # Habla el listener 1883 EN CLARO Y ANÓNIMO, no el 8883 del GtD. No es un
    # descuido: es el único que la app vieja sabe hablar (`kBrokerHost` /
    # `kBrokerPort` están compilados adentro del APK). Por eso son campos
    # propios y no reusa mqtt_host/mqtt_port — son dos políticas opuestas y
    # confundirlas dejaría al GtD hablando sin TLS o al puente sin conectar.
    legacy_mqtt_host: str = "localhost"
    legacy_mqtt_port: int = 1883
    legacy_mqtt_client_id: str = "gtd-legacy-1"
    legacy_topic: str = "cliente/servidor"

    # Rol `cps_legacy`: EXECUTE sobre gtd.enqueue_legacy_alarm y NADA más — ni
    # siquiera SELECT sobre `device`. Vacío ⇒ PuertaStub, que acepta y tira: el
    # proceso avisa fuerte al arrancar.
    #
    # NO cae a pg_dsn a propósito (a diferencia del provisioner): ese es el rol
    # del GtD, y darle la entrada anónima de internet al rol que escribe el
    # estado vivo de la flota es exactamente lo que este diseño evita.
    legacy_dsn: str = ""

    # Freno anti-abuso. El 1883 es anónimo y la app vieja no autentica a nadie,
    # así que cualquiera puede publicar a nombre de cualquier DNI. Ver freno.py.
    # El cps999 (desactivar) NUNCA se frena.
    legacy_freno_dni_s: float = 3.0
    legacy_freno_global_por_min: int = 30

    # ── La BAJADA: proyección a Firebase + push ──────────────────────────
    # Service account de `cpssecurityapp` (el proyecto que lee la app vieja).
    # Vacío = no se proyecta nada: el puente sirve solo para activar, y la app
    # queda con las pantallas congeladas. El proceso avisa al arrancar.
    legacy_sa_file: str = ""
    legacy_rtdb_url: str = "https://cpssecurityapp-default-rtdb.firebaseio.com"

    # Prefijo de ENSAYO. Vacío = producción, o sea los paths que los teléfonos
    # de los vecinos están escuchando AHORA. Con algo (ej. "_ensayo") todo se
    # escribe abajo de ese nodo y no lo ve nadie: es la única forma de probar la
    # proyección sin mostrarle una activación falsa a un barrio entero.
    legacy_rtdb_prefijo: str = ""

    # El push es lo ÚNICO que hoy le avisa a un vecino que sonó la alarma.
    # Apagable aparte de la proyección para poder ensayar sin notificar.
    legacy_push: bool = True

    # Reconciliación. LISTEN/NOTIFY no tiene memoria: un aviso emitido mientras
    # el proceso estaba caído no vuelve nunca, y sin barrido un reinicio en el
    # momento equivocado deja la app diciendo 'Conectada' con una emergencia
    # abierta.
    legacy_barrido_s: float = 30.0
    # El catálogo cambia con altas y suspensiones, no con emergencias: mucho
    # más lento a propósito.
    legacy_clientes_s: float = 300.0

    # Observabilidad
    log_level: str = "INFO"

    @property
    def use_postgres(self) -> bool:
        return bool(self.pg_dsn)
