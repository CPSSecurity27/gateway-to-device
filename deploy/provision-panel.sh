#!/usr/bin/env bash
#
# Da de alta un panel en el broker (listener 8883): usuario, password y ACL.
#
#   sudo -E bash deploy/provision-panel.sh AA:BB:CC:DD:EE:FF
#
# La password NO se inventa: se calcula con el MISMO algoritmo que el firmware
# (docs/02-provisioning-auth.md), así que este script también sirve para verificar
# que los dos lados coinciden byte a byte.
#
#     usuario  = "AV-" + hex_mayus(MAC_STA)
#     password = "SCPS-" + hex_mayus( HMAC-SHA256(SALT_MQTT, MAC_STA)[0..11] )
#
# El SALT_MQTT se pide por consola (no se ve al tipear) o se toma de la variable
# de entorno SALT_MQTT. NUNCA pasarlo como argumento: quedaría en el historial del
# shell y en la lista de procesos.
#
# Idempotente: re-registrar el mismo panel recalcula lo mismo y no duplica la ACL.

set -euo pipefail

APP_DIR="/home/servidorcps/SistemaCPS/gateway-to-device"
PASSWD_FILE="/etc/mosquitto/gtd.passwd"
ACL_FILE="/etc/mosquitto/gtd.acl"

log()  { echo -e "\n\033[1;36m==>\033[0m $*"; }
ok()   { echo -e "  \033[32m✓\033[0m $*"; }
warn() { echo -e "  \033[33m!\033[0m $*"; }
die()  { echo -e "\n\033[31m✗ $*\033[0m" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Correr con sudo."
[ $# -ge 1 ] || die "Uso: sudo -E bash $0 <MAC>   (ej: AA:BB:CC:DD:EE:FF)"

RAW_MAC="$1"; shift

# ── Modo y flags ────────────────────────────────────────────────────
# `revoke` da de baja la credencial. Los flags existen para el provisioner
# (`python -m gtd.provisioner`): con una tanda de equipos, recargar y verificar
# UNA vez al final en vez de por equipo. Ver la spec del provisioner en el repo
# web: docs/superpowers/specs/2026-08-04-provisioner-broker-design.md
MODO_OP="provision"
DO_RELOAD=1
DO_PROBE=1

if [ "$RAW_MAC" = "revoke" ]; then
  MODO_OP="revoke"
  RAW_MAC="${1:-}"; shift || true
  [ -n "$RAW_MAC" ] || die "Uso: sudo -E bash $0 revoke <MAC>"
fi

TOPIC_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-reload) DO_RELOAD=0 ;;
    --no-probe)  DO_PROBE=0 ;;
    *)           TOPIC_ARGS+=("$1") ;;
  esac
  shift
done
set -- "${TOPIC_ARGS[@]+"${TOPIC_ARGS[@]}"}"

# Normalización: 12 dígitos hex en mayúsculas, sin separadores.
HEX_MAC="$(echo "$RAW_MAC" | tr -d ':-' | tr '[:lower:]' '[:upper:]')"
[[ "$HEX_MAC" =~ ^[0-9A-F]{12}$ ]] || die "MAC inválida: '$RAW_MAC' (esperaba 6 bytes hex)."
USERNAME="AV-${HEX_MAC}"

# El device_id del tópico: el contrato dice `av/<id>` pero no fija el formato, y
# el firmware imprime la identidad de tres maneras distintas. Por defecto se
# habilitan las tres formas plausibles de ESTA MAC — sigue siendo estricto (un
# panel no puede tocar los tópicos de otro) y evita que el test falle por un
# detalle de formato. Se pueden pasar ids explícitos como argumentos extra.
if [ $# -gt 0 ]; then
  TOPIC_IDS=("$@")
else
  MAC_COLON="$(echo "$HEX_MAC" | sed 's/../&:/g; s/:$//')"
  TOPIC_IDS=("$HEX_MAC" "$MAC_COLON" "$USERNAME")
fi

# ── revoke: se va la credencial y listo ─────────────────────────────
# La ACL no se toca: la regla `pattern av/%u/…` es de flota y no nombra equipos.
# Sin usuario en gtd.passwd el panel no puede autenticar, y una regla que se
# resuelve contra %u deja de aplicarle sola.
if [ "$MODO_OP" = "revoke" ]; then
  log "Baja de credencial"
  echo "  MAC     : $HEX_MAC"
  echo "  usuario : $USERNAME"
  [ -f "$PASSWD_FILE" ] || die "No existe $PASSWD_FILE."

  if mosquitto_passwd -D "$PASSWD_FILE" "$USERNAME" 2>/dev/null; then
    ok "usuario $USERNAME eliminado"
  else
    warn "el usuario $USERNAME no estaba en el archivo (nada que hacer)"
  fi

  if [ "$DO_RELOAD" -eq 1 ]; then
    log "Recargando mosquitto"
    systemctl reload mosquitto 2>/dev/null || systemctl restart mosquitto
    sleep 2
    systemctl is-active --quiet mosquitto || die "mosquitto no quedó activo."
    ok "activo"
  else
    ok "reload omitido (--no-reload): recordá recargar al final del lote"
  fi
  exit 0
fi

# La password: normalmente la calcula el contrato (HMAC). Pero un build en MODO
# LABORATORIO usa una fija — en ese caso se pasa por PANEL_PASSWORD y no se pide
# el salt, porque no interviene.
if [ -n "${PANEL_PASSWORD:-}" ]; then
  PASSWORD="$PANEL_PASSWORD"
  MODO="explícita (build de laboratorio)"
else
  # El doc del equipo de servidor la llama MQTT_DERIV_SALT; el firmware, SALT_MQTT.
  # Es el mismo secreto: se acepta cualquiera de los dos nombres.
  SALT="${MQTT_DERIV_SALT:-${SALT_MQTT:-}}"
  if [ -z "$SALT" ]; then
    echo -n "SALT de derivación (no se muestra al tipear): "
    read -rs SALT
    echo
  fi
  [ -n "$SALT" ] || die "Salt vacío (o exportar PANEL_PASSWORD si el build es LAB)."

  # En Python y no en openssl: la clave y el mensaje son bytes crudos y no quiero
  # que el shell interprete nada del salt.
  #
  # Antes de derivar nada se valida el salt contra el VECTOR DE VERIFICACIÓN del
  # doc de provisioning. Sin esto, un salt equivocado registra credenciales que
  # parecen válidas y fallan recién cuando el panel intenta conectar — que es el
  # peor momento para enterarse.
  KAT_MAC="A842E38FCA6C"
  KAT_PASS="4EA453D76DD9E1C81A0D141B"
  PREFIJO="${MQTT_PASS_PREFIX:-}"      # el doc de servidor no lleva prefijo

  read -r KAT_CALC PASSWORD <<EOF
$(SALT="$SALT" HEXMAC="$HEX_MAC" KATMAC="$KAT_MAC" PRE="$PREFIJO" python3 - <<'PYEOF'
import hashlib, hmac, os
salt = os.environ["SALT"].encode()          # sin el NUL final (strlen en C)
pre  = os.environ["PRE"]
def deriv(hexmac: str) -> str:
    mac = bytes.fromhex(hexmac)             # los 6 bytes crudos, no el string
    return pre + hmac.new(salt, mac, hashlib.sha256).digest()[:12].hex().upper()
print(deriv(os.environ["KATMAC"]), deriv(os.environ["HEXMAC"]))
PYEOF
)
EOF

  if [ "$KAT_CALC" != "${PREFIJO}${KAT_PASS}" ]; then
    echo
    echo "  vector esperado : ${PREFIJO}${KAT_PASS}   (MAC ${KAT_MAC})"
    echo "  calculado       : ${KAT_CALC}"
    die "El salt NO reproduce el vector de verificación del doc de provisioning.
   O el salt no es el de producción, o el formato de la password difiere.
   NO se registró nada. Ver docs/02-provisioning-auth.md §Derivación."
  fi
  ok "salt validado contra el vector del doc (MAC ${KAT_MAC})"
  MODO="HMAC-SHA256 derivada del salt"
fi
[ -n "$PASSWORD" ] || die "Password vacía."

log "Panel a registrar"
echo "  MAC       : $HEX_MAC"
echo "  usuario   : $USERNAME"
echo "  password  : $MODO"
echo "  tópicos   : ${TOPIC_IDS[*]}"

# ── Alta en el broker ───────────────────────────────────────────────
log "Credencial"
[ -f "$PASSWD_FILE" ] || die "No existe $PASSWD_FILE. Correr antes deploy/install-root.sh."
mosquitto_passwd -b "$PASSWD_FILE" "$USERNAME" "$PASSWORD"
chown root:mosquitto "$PASSWD_FILE"; chmod 0640 "$PASSWD_FILE"
ok "usuario $USERNAME cargado"

log "ACL"
# No se toca: `deploy/gtd.acl` tiene una regla `pattern av/%u/…` que cubre a toda
# la flota. El usuario ES el <id> del tópico, así que el panel queda encerrado en
# los suyos automáticamente. Un archivo que no crece con la flota es un archivo
# que no se desincroniza.
grep -q "pattern write av/%u/status" "$ACL_FILE" \
  && ok "cubierto por la regla de flota (nada que agregar)" \
  || die "la ACL no tiene las reglas pattern. Correr antes deploy/apply-acl.sh."

if [ "$DO_RELOAD" -eq 1 ]; then
  log "Recargando mosquitto"
  systemctl reload mosquitto 2>/dev/null || systemctl restart mosquitto
  sleep 2
  systemctl is-active --quiet mosquitto || die "mosquitto no quedó activo."
  ok "activo"
else
  ok "reload omitido (--no-reload): recordá recargar al final del lote"
fi

# ── Verificación: la credencial entra de verdad ─────────────────────
# OJO: esto publica un `status` REAL en el broker. Con un equipo suelto es una
# verificación de punta a punta; con una TANDA, el GtD recibiría un status por
# equipo y marcaría a todos como conectados, escribiéndoles `first_connection_at`
# con los paneles todavía en la caja. El hito de primera conexión es un hecho
# observado: ensuciarlo con una prueba de laboratorio lo vuelve inútil.
# Por eso el provisioner pasa --no-probe y verifica una sola vez, al final.
if [ "$DO_PROBE" -eq 1 ]; then
  log "Probando la credencial contra 8883"
  PROBE_ID="${TOPIC_IDS[0]}"
  if timeout 10 mosquitto_pub -h cpssecurity.com.ar -p 8883 \
       -u "$USERNAME" -P "$PASSWORD" \
       -t "av/${PROBE_ID}/status" \
       -m '{"v":1,"estado":"online","modo":"PROVISION_TEST","ts":0}' 2>/dev/null; then
    ok "el panel puede autenticar y publicar su status"
    sleep 2
    if journalctl -u gateway-to-device --no-pager -n 10 -o cat | grep -q "$PROBE_ID"; then
      ok "y el GtD lo recibió — camino completo verificado"
    else
      warn "el GtD no lo vio (raro: el id '$PROBE_ID' está en la ACL)"
    fi
  else
    die "la credencial no entra. Revisar: journalctl -u mosquitto -n 30"
  fi
else
  ok "verificación omitida (--no-probe)"
fi

log "LISTO — encender la placa y mirar"
cat <<EOF

    journalctl -u gateway-to-device -f | grep --line-buffered -E 'panel |evento|descartado'

  El panel debe autenticar como ${USERNAME}. Si el broker lo rechaza, la password
  del build no es la que se cargó acá.
EOF
