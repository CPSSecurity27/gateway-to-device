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

# La password: normalmente la calcula el contrato (HMAC). Pero un build en MODO
# LABORATORIO usa una fija — en ese caso se pasa por PANEL_PASSWORD y no se pide
# el salt, porque no interviene.
if [ -n "${PANEL_PASSWORD:-}" ]; then
  PASSWORD="$PANEL_PASSWORD"
  MODO="explícita (build de laboratorio)"
else
  if [ -z "${SALT_MQTT:-}" ]; then
    echo -n "SALT_MQTT (no se muestra al tipear): "
    read -rs SALT_MQTT
    echo
  fi
  [ -n "$SALT_MQTT" ] || die "SALT_MQTT vacío (o exportar PANEL_PASSWORD si el build es LAB)."

  # En Python y no en openssl: la clave y el mensaje son bytes crudos y no quiero
  # que el shell interprete nada del salt.
  PASSWORD="$(SALT="$SALT_MQTT" HEXMAC="$HEX_MAC" python3 - <<'PYEOF'
import hashlib, hmac, os
salt = os.environ["SALT"].encode()          # sin el NUL final (strlen en C)
mac  = bytes.fromhex(os.environ["HEXMAC"])  # los 6 bytes crudos
h    = hmac.new(salt, mac, hashlib.sha256).digest()
print("SCPS-" + h[:12].hex().upper())
PYEOF
)"
  MODO="HMAC-SHA256 del contrato"
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
if grep -q "^user ${USERNAME}$" "$ACL_FILE"; then
  ok "ya tenía reglas (sin cambios)"
else
  {
    echo
    echo "# Panel ${HEX_MAC} — alta $(date '+%Y-%m-%d') por deploy/provision-panel.sh."
    echo "# Sube su estado, baja sus órdenes. Solo SUS tópicos: un panel no puede leer"
    echo "# ni escribir los de otro."
    echo "user ${USERNAME}"
    for id in "${TOPIC_IDS[@]}"; do
      echo "topic write av/${id}/status"
      echo "topic write av/${id}/tele"
      echo "topic write av/${id}/up"
      echo "topic read  av/${id}/cmd"
      echo "topic read  av/${id}/cfg"
    done
    echo "# Broadcast a la flota (S→D)."
    echo "topic read av/all/cmd"
  } >> "$ACL_FILE"
  ok "reglas agregadas para ${#TOPIC_IDS[@]} formato(s) de id"
fi

log "Recargando mosquitto"
systemctl reload mosquitto 2>/dev/null || systemctl restart mosquitto
sleep 2
systemctl is-active --quiet mosquitto || die "mosquitto no quedó activo."
ok "activo"

# ── Verificación: la credencial entra de verdad ─────────────────────
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

log "LISTO — encender la placa y mirar"
cat <<EOF

    journalctl -u gateway-to-device -f | grep --line-buffered -E 'panel |evento|descartado'

  El panel debe autenticar como ${USERNAME}. Si el broker lo rechaza, la password
  del build no es la que se cargó acá.
EOF
