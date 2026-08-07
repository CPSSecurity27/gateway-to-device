#!/usr/bin/env bash
#
# Aplica deploy/gtd.acl al listener 8883 y VERIFICA que encierre a cada panel en
# sus propios tópicos.
#
#   sudo bash deploy/apply-acl.sh
#
# Reemplaza la ACL entera, incluidas las reglas por panel que dejaba la versión
# vieja de provision-panel.sh: ahora una sola regla `pattern` cubre a toda la
# flota. Nada de esto toca el 1883 ni las credenciales ya cargadas.
#
# Idempotente. Con rollback si la verificación falla.

set -euo pipefail

APP_DIR="/home/servidorcps/SistemaCPS/gateway-to-device"
ACL_FILE="/etc/mosquitto/gtd.acl"
BACKUP="/root/gtd-acl-$(date +%Y%m%d-%H%M%S).bak"
HOST="cpssecurity.com.ar"

# Panel de prueba. Cambiar si se verifica con otro (tiene que estar dado de alta).
TEST_USER="${TEST_USER:-AV-240AC4000110}"
TEST_PASS="${TEST_PASS:-dev-sin-secreto}"

log()  { echo -e "\n\033[1;36m==>\033[0m $*"; }
ok()   { echo -e "  \033[32m✓\033[0m $*"; }
warn() { echo -e "  \033[33m!\033[0m $*"; }
die()  { echo -e "\n\033[31m✗ $*\033[0m" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Correr con sudo."
[ -f "$ACL_FILE" ] || die "No existe $ACL_FILE. Correr antes deploy/install-root.sh."

log "Backup"
cp -a "$ACL_FILE" "$BACKUP"; ok "$BACKUP"

rollback() {
  warn "REVIRTIENDO la ACL…"
  cp -a "$BACKUP" "$ACL_FILE"
  systemctl restart mosquitto
  sleep 2
}

log "Instalando la ACL nueva"
install -o root -g mosquitto -m 0640 "$APP_DIR/deploy/gtd.acl" "$ACL_FILE"
# restart y no reload: el SIGHUP deja el contexto TLS a medias y los paneles
# dejan de completar el handshake (2026-08-06). Ver broker.py::recargar.
systemctl restart mosquitto
sleep 2
systemctl is-active --quiet mosquitto || { rollback; die "mosquitto no quedó activo."; }
ok "cargada"

# ── Verificación ────────────────────────────────────────────────────
# El broker descarta un publish no autorizado en silencio (el cliente no siempre
# se entera), así que se verifica por el efecto: qué recibió el GtD.
vio_el_gtd() {  # $1 = marca a buscar, $2 = desde
  journalctl -u gateway-to-device --since "$2" --no-pager -o cat | grep -q "$1"
}

log "1/3 — el panel puede publicar en SUS tópicos"
SINCE="$(date '+%Y-%m-%d %H:%M:%S')"; sleep 1
timeout 10 mosquitto_pub -h "$HOST" -p 8883 -u "$TEST_USER" -P "$TEST_PASS" \
  -t "av/${TEST_USER}/status" \
  -m '{"v":1,"estado":"online","modo":"ACL_TEST","ts":1}' 2>/dev/null \
  || { rollback; die "el panel NO puede publicar en su propio status."; }
sleep 3
vio_el_gtd "ACL_TEST" "$SINCE" || { rollback; die "el GtD no recibió el status propio del panel."; }
ok "publica en av/${TEST_USER}/status y el GtD lo recibe"

log "2/3 — el panel NO puede publicar en los tópicos de OTRO"
INTRUSO="AV-DEADBEEF0000"
SINCE="$(date '+%Y-%m-%d %H:%M:%S')"; sleep 1
timeout 10 mosquitto_pub -h "$HOST" -p 8883 -u "$TEST_USER" -P "$TEST_PASS" \
  -t "av/${INTRUSO}/status" \
  -m '{"v":1,"estado":"online","modo":"ACL_INTRUSION","ts":1}' 2>/dev/null || true
sleep 3
if vio_el_gtd "ACL_INTRUSION" "$SINCE"; then
  rollback
  die "FALLA DE AISLAMIENTO: un panel pudo escribir en los tópicos de otro."
fi
ok "el broker bloqueó av/${INTRUSO}/status"

log "3/3 — el GtD sigue con sus permisos"
timeout 10 mosquitto_pub -h "$HOST" -p 8883 \
  -u gateway -P "$(grep '^GTD_MQTT_PASSWORD=' "$APP_DIR/.env" | cut -d= -f2-)" \
  -t "av/${TEST_USER}/cmd" -m '{"acl":"test"}' 2>/dev/null \
  || { rollback; die "el gateway ya no puede publicar en av/+/cmd."; }
ok "el gateway publica en av/+/cmd"

systemctl is-active --quiet gateway-to-device || warn "el GtD no está activo (revisar aparte)"

log "LISTO"
cat <<EOF

  La ACL ya no crece con la flota: una regla \`pattern\` encierra a cada panel en
  av/<su-usuario>/*. Dar de alta un panel = cargar su password, nada más.

  Backup de la anterior: ${BACKUP}

EOF
