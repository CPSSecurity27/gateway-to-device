#!/usr/bin/env bash
#
# Cierra la puerta lateral del 1883 hacia el espacio `av/` de los paneles nuevos.
#
#   sudo bash deploy/secure-av-namespace.sh
#
# El broker es UN bus compartido: los listeners están separados en seguridad
# (per_listener_settings), no en tópicos. Mientras el 1883 sea anónimo, cualquiera
# publica en av/<MAC>/cmd desde internet y un panel en 8883 obedece — la ACL del
# listener autenticado no sirve de nada.
#
# Este script le pone al 1883 una ACL que niega `av/#` y deja TODO lo demás
# abierto, tal cual está hoy. Las centrales (CENTRALVECINAL05/06) y el
# broker-bridge no se ven afectados: nunca usan `av/`.
#
# Toca UNA línea de default.conf (el acl_file del listener 1883). Verifica que el
# tráfico viejo siga fluyendo y que el deny funcione; si algo falla, revierte.
#
# Idempotente.

set -euo pipefail

APP_DIR="/home/servidorcps/SistemaCPS/gateway-to-device"
MOSQ_CONF_D="/etc/mosquitto/conf.d"
DEFAULT_CONF="${MOSQ_CONF_D}/default.conf"
LEGACY_ACL="/etc/mosquitto/legacy-1883.acl"
BACKUP="/root/gtd-backup-$(date +%Y%m%d-%H%M%S)-acl1883"

log()  { echo -e "\n\033[1;36m==>\033[0m $*"; }
ok()   { echo -e "  \033[32m✓\033[0m $*"; }
warn() { echo -e "  \033[33m!\033[0m $*"; }
die()  { echo -e "\n\033[31m✗ $*\033[0m" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Correr con sudo."
[ -f "$DEFAULT_CONF" ] || die "No existe $DEFAULT_CONF."
grep -q "per_listener_settings true" "$MOSQ_CONF_D"/*.conf \
  || die "Falta per_listener_settings. Correr antes deploy/install-root.sh."

log "Backup en $BACKUP"
mkdir -p "$BACKUP"; cp -a "$MOSQ_CONF_D" "$BACKUP/conf.d"
ok "guardado"

rollback() {
  warn "REVIRTIENDO…"
  cp -a "$BACKUP/conf.d/." "$MOSQ_CONF_D/"
  systemctl restart mosquitto || true; sleep 3
  systemctl is-active --quiet mosquitto \
    && warn "mosquitto volvió a su estado anterior." \
    || die "mosquitto no levanta ni revertido. Ver: journalctl -u mosquitto -n 50"
}

# ── 1. ACL del listener viejo ───────────────────────────────────────
log "ACL del 1883"
install -o root -g mosquitto -m 0640 "$APP_DIR/deploy/legacy-1883.acl" "$LEGACY_ACL"
ok "instalada en $LEGACY_ACL"

# El acl_file tiene que quedar DENTRO del bloque del listener 1883: con
# per_listener_settings, las opciones pertenecen al listener que las precede.
if grep -q "^acl_file ${LEGACY_ACL}$" "$DEFAULT_CONF"; then
  ok "default.conf ya la referencia (nada que hacer)"
else
  grep -q "^listener 1883" "$DEFAULT_CONF" || { rollback; die "No encuentro 'listener 1883' en default.conf."; }
  # Se inserta justo después de allow_anonymous, que es la última línea del bloque.
  sed -i "/^allow_anonymous true/a\\
\\
# ACL del puerto en claro — la instala el GtD (deploy/secure-av-namespace.sh).\\
# Niega av/# y deja el resto abierto: el sistema viejo sigue igual.\\
acl_file ${LEGACY_ACL}" "$DEFAULT_CONF"
  ok "agregado acl_file a default.conf"
fi

log "Reiniciando mosquitto"
systemctl restart mosquitto; sleep 3
systemctl is-active --quiet mosquitto || { rollback; die "mosquitto no arrancó."; }
ok "mosquitto activo"

# ── 2. Verificar que el sistema VIEJO sigue intacto ─────────────────
log "Verificando el sistema viejo (hasta 90s esperando a las centrales)"

if ! timeout 10 mosquitto_pub -h 127.0.0.1 -p 1883 -t 'gtd/selftest' -m ok 2>/dev/null; then
  rollback; die "el 1883 dejó de aceptar publicaciones anónimas."
fi
ok "1883 sigue aceptando anónimos"

LEGACY="$(timeout 90 mosquitto_sub -h 127.0.0.1 -p 1883 -t '#' -v 2>/dev/null \
          | grep -vE '^(av/|gtd/)' | head -3)"
if [ -z "$LEGACY" ]; then
  rollback
  die "NINGUNA central publicó en 90s. Antes lo hacían — se asume que se rompió."
fi
echo "$LEGACY" | sed 's/^/      /'
ok "las centrales siguen publicando"

systemctl is-active --quiet broker-bridge || { rollback; die "broker-bridge se cayó."; }
ok "broker-bridge sigue corriendo"

# ── 3. Verificar que el deny funciona de verdad ─────────────────────
# Prueba real de punta a punta: si un anónimo del 1883 pudiera publicar en av/,
# el GtD (suscripto en 8883) lo loguearía. No debe aparecer nada.
log "Verificando que av/ quedó cerrado desde el 1883"
FAKE="DENYTEST-$(date +%s)"
SINCE="$(date '+%Y-%m-%d %H:%M:%S')"
sleep 1
timeout 10 mosquitto_pub -h 127.0.0.1 -p 1883 -t "av/${FAKE}/status" \
  -m '{"v":1,"estado":"online","modo":"X","ts":1}' 2>/dev/null || true
sleep 3
if journalctl -u gateway-to-device --since "$SINCE" --no-pager -o cat 2>/dev/null | grep -q "$FAKE"; then
  rollback; die "el deny NO funciona: el GtD recibió un mensaje av/ publicado desde el 1883."
fi
ok "el 1883 ya no puede publicar en av/ (el GtD no lo recibió)"

log "Verificando que el 8883 SÍ sigue funcionando"
GW_PASS="$(grep '^GTD_MQTT_PASSWORD=' "$APP_DIR/.env" | cut -d= -f2-)"
timeout 10 mosquitto_pub -h cpssecurity.com.ar -p 8883 -u gateway -P "$GW_PASS" \
  -t 'av/SELFTEST/cmd' -m '{"selftest":1}' 2>/dev/null \
  || { rollback; die "el gateway ya no puede publicar en 8883."; }
ok "el GtD sigue autenticando y publicando en 8883"

systemctl is-active --quiet gateway-to-device || { rollback; die "el GtD se cayó."; }
ok "gateway-to-device sigue activo"

log "LISTO"
cat <<EOF

  1883  anónimo, TODO abierto MENOS av/#   (centrales + broker-bridge, sin cambios)
  8883  TLS autenticado, ACL por usuario   (paneles nuevos + GtD)

  El espacio av/ ahora es alcanzable SOLO por 8883 autenticado.
  Backup: ${BACKUP}

EOF
