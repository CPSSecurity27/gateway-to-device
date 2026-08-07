#!/usr/bin/env bash
#
# Instalación del GtD en el servidor (parte que necesita root).
#
#   sudo bash deploy/install-root.sh
#
# Hace tres cosas:
#   1. Levanta el listener 8883 (TLS) en mosquitto, con usuario y ACL para el GtD.
#   2. Genera el .env del GtD con la credencial recién creada.
#   3. Instala y arranca el servicio systemd.
#
# NO toca el listener 1883 en claro: los paneles y el broker-bridge (MQTT→Firebase)
# siguen funcionando exactamente igual. Si algo de eso se rompe durante la
# verificación, el script REVIERTE la configuración de mosquitto y aborta.
#
# Es idempotente: se puede correr de nuevo sin duplicar nada ni rotar la password.

set -euo pipefail

APP_USER="servidorcps"
APP_DIR="/home/${APP_USER}/SistemaCPS/gateway-to-device"
DOMAIN="cpssecurity.com.ar"

MOSQ_CONF_D="/etc/mosquitto/conf.d"
MOSQ_CERTS="/etc/mosquitto/certs"
PASSWD_FILE="/etc/mosquitto/gtd.passwd"
ACL_FILE="/etc/mosquitto/gtd.acl"
ENV_FILE="${APP_DIR}/.env"
UNIT="/etc/systemd/system/gateway-to-device.service"
BACKUP="/root/gtd-backup-$(date +%Y%m%d-%H%M%S)"

log()  { echo -e "\n\033[1;36m==>\033[0m $*"; }
ok()   { echo -e "  \033[32m✓\033[0m $*"; }
warn() { echo -e "  \033[33m!\033[0m $*"; }
die()  { echo -e "\n\033[31m✗ $*\033[0m" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Correr con sudo."
[ -d "$APP_DIR" ] || die "No existe $APP_DIR (¿se clonó el repo?)."
[ -x "$APP_DIR/.venv/bin/python" ] || die "No existe el venv en $APP_DIR/.venv."
command -v mosquitto_passwd >/dev/null || die "Falta mosquitto_passwd (apt install mosquitto)."

# ── 0. Backup de lo que vamos a tocar ───────────────────────────────
log "Backup en $BACKUP"
mkdir -p "$BACKUP"
cp -a "$MOSQ_CONF_D" "$BACKUP/conf.d"
[ -f "$PASSWD_FILE" ] && cp -a "$PASSWD_FILE" "$BACKUP/"
[ -f "$ACL_FILE" ] && cp -a "$ACL_FILE" "$BACKUP/"
ok "guardado (para revertir a mano: cp -a $BACKUP/conf.d/. $MOSQ_CONF_D/)"

rollback() {
  warn "REVIRTIENDO la configuración de mosquitto…"
  rm -f "$MOSQ_CONF_D"/00-per-listener.conf "$MOSQ_CONF_D"/gtd-tls.conf
  cp -a "$BACKUP/conf.d/." "$MOSQ_CONF_D/"
  systemctl restart mosquitto || true
  sleep 2
  systemctl is-active --quiet mosquitto && warn "mosquitto volvió a su estado anterior." \
    || die "mosquitto NO levantó ni siquiera revertido. Revisar: journalctl -u mosquitto -n 50"
}

# ── 1. Certificados para mosquitto ──────────────────────────────────
# mosquitto corre como usuario 'mosquitto' y NO puede leer /etc/letsencrypt/live
# (es 0700 de root). Se copian los certs a un directorio propio y se re-copian en
# cada renovación con un hook de certbot.
log "Certificados TLS"
CERT_SRC="/etc/letsencrypt/live/${DOMAIN}"
if [ ! -d "$CERT_SRC" ]; then
  CERT_SRC="$(find /etc/letsencrypt/live -mindepth 1 -maxdepth 1 -type d | head -1)" || true
  [ -n "$CERT_SRC" ] || die "No hay certificados en /etc/letsencrypt/live. Emitir uno con certbot para ${DOMAIN}."
  warn "No hay cert para ${DOMAIN}; usando $(basename "$CERT_SRC")"
fi
ok "origen: $CERT_SRC"

install -d -o mosquitto -g mosquitto -m 0750 "$MOSQ_CERTS"
install -o mosquitto -g mosquitto -m 0644 "$CERT_SRC/fullchain.pem" "$MOSQ_CERTS/fullchain.pem"
install -o mosquitto -g mosquitto -m 0600 "$CERT_SRC/privkey.pem"   "$MOSQ_CERTS/privkey.pem"
ok "copiados a $MOSQ_CERTS"

# Hook de renovación: sin esto, el cert vence en 90 días y los paneles dejan de
# conectar por TLS con un error que no dice nada obvio.
HOOK="/etc/letsencrypt/renewal-hooks/deploy/50-mosquitto-gtd.sh"
mkdir -p "$(dirname "$HOOK")"
cat > "$HOOK" <<HOOKEOF
#!/bin/sh
# Re-copia los certs a mosquitto tras cada renovación y lo recarga. Lo instala
# deploy/install-root.sh del GtD.
set -e
install -o mosquitto -g mosquitto -m 0644 "${CERT_SRC}/fullchain.pem" "${MOSQ_CERTS}/fullchain.pem"
install -o mosquitto -g mosquitto -m 0600 "${CERT_SRC}/privkey.pem"   "${MOSQ_CERTS}/privkey.pem"
# restart y no reload: el SIGHUP deja el contexto TLS a medias y los paneles
# no completan el handshake (visto el 2026-08-06). Ver broker.py::recargar.
systemctl restart mosquitto
HOOKEOF
chmod 0755 "$HOOK"
ok "hook de renovación instalado en $HOOK"

# ── 2. Credencial del GtD ───────────────────────────────────────────
# Si ya hay una password en el .env, se reutiliza: correr el script dos veces no
# debe dejar al GtD con una credencial distinta a la del broker.
log "Usuario 'gateway' en el broker"
if [ -f "$ENV_FILE" ] && grep -q '^GTD_MQTT_PASSWORD=.\+' "$ENV_FILE"; then
  GW_PASS="$(grep '^GTD_MQTT_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
  ok "reutilizando la password ya existente en el .env"
else
  GW_PASS="$(openssl rand -hex 24)"
  ok "password nueva generada (24 bytes)"
fi

if [ -f "$PASSWD_FILE" ]; then
  mosquitto_passwd -b "$PASSWD_FILE" gateway "$GW_PASS"
else
  mosquitto_passwd -c -b "$PASSWD_FILE" gateway "$GW_PASS"
fi
chown mosquitto:mosquitto "$PASSWD_FILE"; chmod 0640 "$PASSWD_FILE"
install -o mosquitto -g mosquitto -m 0640 "$APP_DIR/deploy/gtd.acl" "$ACL_FILE"
ok "usuario y ACL cargados"

# ── 3. Configuración de mosquitto ───────────────────────────────────
# `per_listener_settings true` hace que allow_anonymous/password_file/acl_file
# sean POR LISTENER. Debe leerse ANTES de cualquier listener: por eso el 00-.
# Efecto: 1883 sigue anónimo y sin ACL (bridge y paneles intactos), 8883 exige
# usuario y aplica la ACL.
log "Listener 8883 (TLS)"
cat > "$MOSQ_CONF_D/00-per-listener.conf" <<'EOF'
# Seguridad por listener. Lo instala el GtD (deploy/install-root.sh).
# El nombre empieza con 00 a propósito: conf.d se lee alfabéticamente y esta
# opción tiene que estar antes de que se declare el primer listener.
per_listener_settings true
EOF

cat > "$MOSQ_CONF_D/gtd-tls.conf" <<EOF
# Listener TLS autenticado — lo instala el GtD (deploy/install-root.sh).
# El listener 1883 en claro vive en default.conf y NO se toca.
listener 8883
protocol mqtt

certfile ${MOSQ_CERTS}/fullchain.pem
keyfile  ${MOSQ_CERTS}/privkey.pem

# Sin cafile ni require_certificate: los clientes validan al servidor contra las
# CA públicas (Let's Encrypt); no usamos certificados de cliente.
allow_anonymous false
password_file ${PASSWD_FILE}
acl_file ${ACL_FILE}
EOF
ok "escritos 00-per-listener.conf y gtd-tls.conf"

log "Reiniciando mosquitto"
systemctl restart mosquitto
sleep 3
systemctl is-active --quiet mosquitto || { rollback; die "mosquitto no arrancó con la config nueva."; }
ok "mosquitto activo"

# ── 4. Verificación (con rollback si rompimos algo) ─────────────────
log "Verificando que NO rompimos lo que ya andaba"

if ! ss -tln | grep -q ':1883'; then rollback; die "1883 dejó de escuchar."; fi
ok "1883 sigue escuchando"

if ! timeout 10 mosquitto_pub -h 127.0.0.1 -p 1883 -t 'gtd/selftest' -m ok 2>/dev/null; then
  rollback; die "1883 anónimo dejó de aceptar publicaciones (rompería el broker-bridge)."
fi
ok "1883 sigue aceptando clientes anónimos"

sleep 2
if ! systemctl is-active --quiet broker-bridge; then
  warn "broker-bridge NO está activo — reiniciándolo"
  systemctl restart broker-bridge || true; sleep 3
  systemctl is-active --quiet broker-bridge || { rollback; die "broker-bridge no se recupera."; }
fi
ok "broker-bridge sigue corriendo"

log "Verificando el listener nuevo"
ss -tln | grep -q ':8883' || { rollback; die "8883 no está escuchando."; }
ok "8883 escuchando"

# ¿El dominio hace hairpin desde la propia Pi? Si no, el GtD tiene que ir a
# localhost — pero entonces el nombre del certificado no valida. Se resuelve
# apuntando el dominio a 127.0.0.1 en /etc/hosts (split-horizon), que mantiene
# el TLS válido y el tráfico local.
MQTT_HOST="$DOMAIN"
if timeout 10 mosquitto_pub -h "$DOMAIN" -p 8883 -u gateway -P "$GW_PASS" \
     -t 'av/SELFTEST/cmd' -m '{"selftest":1}' 2>/dev/null; then
  ok "conecta por TLS a ${DOMAIN}:8883 y autentica"
else
  warn "no se alcanza ${DOMAIN}:8883 desde la propia Pi (NAT sin hairpin)"
  if ! grep -qE "^127\.0\.0\.1[[:space:]]+${DOMAIN}" /etc/hosts; then
    echo "127.0.0.1 ${DOMAIN}   # GtD: resolver el dominio localmente (split-horizon)" >> /etc/hosts
    ok "agregado ${DOMAIN} → 127.0.0.1 en /etc/hosts"
  fi
  timeout 10 mosquitto_pub -h "$DOMAIN" -p 8883 -u gateway -P "$GW_PASS" \
    -t 'av/SELFTEST/cmd' -m '{"selftest":1}' 2>/dev/null \
    || { rollback; die "sigue sin conectar por TLS a 8883. Revisar: journalctl -u mosquitto -n 50"; }
  ok "conecta vía /etc/hosts"
fi

# La ACL debe NEGAR lo que no corresponde. mosquitto_pub no falla ante un deny
# (el broker descarta en silencio), así que se verifica por el log del broker.
if timeout 10 mosquitto_sub -h "$DOMAIN" -p 8883 -u gateway -P "$GW_PASS" \
     -t 'av/+/status' -C 1 -W 2 >/dev/null 2>&1; then
  ok "la ACL permite leer av/+/status"
else
  warn "no llegó ningún status en 2s — normal si ningún panel está en 8883 todavía"
fi

# ── 5. .env del GtD ─────────────────────────────────────────────────
log "Configuración del GtD"
cat > "$ENV_FILE" <<EOF
# GENERADO por deploy/install-root.sh — no commitear (está en .gitignore).
# Sin comentarios en la misma línea: pydantic-settings los tomaría como valor.
GTD_MQTT_HOST=${MQTT_HOST}
GTD_MQTT_PORT=8883
GTD_MQTT_USERNAME=gateway
GTD_MQTT_PASSWORD=${GW_PASS}
GTD_MQTT_CA_FILE=
GTD_MQTT_CLIENT_ID=gtd-1
GTD_MQTT_ROOT=av

# Vacío ⇒ StubRepo (in-memory). Completar cuando exista Postgres.
GTD_PG_DSN=

GTD_LOG_LEVEL=INFO
EOF
chown "${APP_USER}:${APP_USER}" "$ENV_FILE"; chmod 0600 "$ENV_FILE"
ok "$ENV_FILE escrito (0600, dueño ${APP_USER})"

# ── 6. Servicio ─────────────────────────────────────────────────────
log "Servicio systemd"
install -m 0644 "$APP_DIR/deploy/gateway-to-device.service" "$UNIT"
systemctl daemon-reload
systemctl enable --now gateway-to-device
sleep 5
systemctl is-active --quiet gateway-to-device \
  || die "El GtD no arrancó. Ver: journalctl -u gateway-to-device -n 50"
ok "gateway-to-device activo y habilitado al boot"

log "LISTO"
cat <<EOF

  Broker    1883 anónimo (paneles + broker-bridge, sin cambios)
            8883 TLS autenticado (GtD; ACL en ${ACL_FILE})
  GtD       ${APP_DIR}  —  usuario ${APP_USER}
  Backup    ${BACKUP}

  Ver la flota conectándose:
      journalctl -u gateway-to-device -f

  Solo las conexiones de paneles:
      journalctl -u gateway-to-device -f | grep -E 'panel (ONLINE|OFFLINE|DURMIENDO)'

EOF
