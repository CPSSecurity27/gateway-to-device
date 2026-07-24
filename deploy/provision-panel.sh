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

RAW_MAC="$1"
# El tópico usa el device_id tal cual lo arma el firmware. Si resulta que no es
# la MAC con dos puntos, se pasa como segundo argumento.
TOPIC_ID="${2:-$RAW_MAC}"

# Normalización: 12 dígitos hex en mayúsculas, sin separadores.
HEX_MAC="$(echo "$RAW_MAC" | tr -d ':-' | tr '[:lower:]' '[:upper:]')"
[[ "$HEX_MAC" =~ ^[0-9A-F]{12}$ ]] || die "MAC inválida: '$RAW_MAC' (esperaba 6 bytes hex)."

if [ -z "${SALT_MQTT:-}" ]; then
  echo -n "SALT_MQTT (no se muestra al tipear): "
  read -rs SALT_MQTT
  echo
fi
[ -n "$SALT_MQTT" ] || die "SALT_MQTT vacío."

# ── Cálculo del contrato ────────────────────────────────────────────
# En Python y no en openssl: la clave y el mensaje son bytes crudos y no quiero
# que el shell interprete nada del salt.
read -r USERNAME PASSWORD <<EOF
$(SALT="$SALT_MQTT" HEXMAC="$HEX_MAC" python3 - <<'PYEOF'
import hashlib, hmac, os
salt = os.environ["SALT"].encode()          # sin el NUL final (strlen en C)
mac  = bytes.fromhex(os.environ["HEXMAC"])  # los 6 bytes crudos
h    = hmac.new(salt, mac, hashlib.sha256).digest()
print(f"AV-{os.environ['HEXMAC']}", "SCPS-" + h[:12].hex().upper())
PYEOF
)
EOF

[ -n "$USERNAME" ] && [ -n "$PASSWORD" ] || die "Falló el cálculo del HMAC."

log "Panel a registrar"
echo "  MAC       : $HEX_MAC"
echo "  usuario   : $USERNAME"
echo "  tópicos   : av/${TOPIC_ID}/{status,tele,up,cmd,cfg}"
echo "  password  : ${PASSWORD:0:11}…  (12 bytes / 96 bits — completa más abajo)"

# ── Alta en el broker ───────────────────────────────────────────────
log "Credencial"
[ -f "$PASSWD_FILE" ] || die "No existe $PASSWD_FILE. Correr antes deploy/install-root.sh."
mosquitto_passwd -b "$PASSWD_FILE" "$USERNAME" "$PASSWORD"
chown root:mosquitto "$PASSWD_FILE"; chmod 0640 "$PASSWD_FILE"
ok "usuario $USERNAME cargado"

log "ACL"
if grep -q "^user ${USERNAME}$" "$ACL_FILE"; then
  ok "ya tenía reglas (sin cambios)"
else
  cat >> "$ACL_FILE" <<EOF

# Panel ${HEX_MAC} — alta $(date '+%Y-%m-%d') por deploy/provision-panel.sh.
# Sube su estado, baja sus órdenes. Solo SUS tópicos: un panel no puede leer ni
# escribir los de otro.
user ${USERNAME}
topic write av/${TOPIC_ID}/status
topic write av/${TOPIC_ID}/tele
topic write av/${TOPIC_ID}/up
topic read  av/${TOPIC_ID}/cmd
topic read  av/${TOPIC_ID}/cfg
EOF
  ok "reglas agregadas"
fi

log "Recargando mosquitto"
systemctl reload mosquitto 2>/dev/null || systemctl restart mosquitto
sleep 2
systemctl is-active --quiet mosquitto || die "mosquitto no quedó activo."
ok "activo"

# ── Verificación: la credencial entra de verdad ─────────────────────
log "Probando la credencial contra 8883"
if timeout 10 mosquitto_pub -h cpssecurity.com.ar -p 8883 \
     -u "$USERNAME" -P "$PASSWORD" \
     -t "av/${TOPIC_ID}/status" \
     -m '{"v":1,"estado":"online","modo":"PROVISION_TEST","ts":0}' 2>/dev/null; then
  ok "el panel puede autenticar y publicar su status"
  sleep 2
  if journalctl -u gateway-to-device --no-pager -n 10 -o cat | grep -q "$TOPIC_ID"; then
    ok "y el GtD lo recibió — camino completo verificado"
  else
    warn "el GtD NO lo vio: revisar que el device_id del tópico sea '$TOPIC_ID'"
  fi
else
  die "la credencial no entra. Revisar: journalctl -u mosquitto -n 30"
fi

log "LISTO — password completa para verificar contra el firmware"
echo
echo "    $PASSWORD"
echo
echo "  Si el panel calcula EXACTAMENTE esta password, los dos lados coinciden."
echo "  Si no, el SALT_MQTT del build no es el que acabás de ingresar."
echo
