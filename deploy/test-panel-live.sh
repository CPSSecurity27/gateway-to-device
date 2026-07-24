#!/usr/bin/env bash
#
# Test del CAMINO DE BAJADA contra una placa real encendida.
#
#   bash deploy/test-panel-live.sh
#
# El otro test (test-e2e.sh) prueba el servidor: se hace pasar por un panel. Este
# prueba la PLACA: le manda órdenes de verdad y verifica que obedezca y conteste.
# Cierra el círculo completo servidor → placa → servidor, con hardware.
#
# Se publica en av/<id>/cmd con la credencial del `gateway` (que tiene permiso de
# escritura ahí). No hace falta Postgres: el downlink del GtD está dormido porque
# no tiene de dónde leer comandos, pero el broker rutea igual.
#
# NO ejecuta `factory` (borra la config de la placa) ni `ota`. `restart` es opt-in.
#
#   WAIT_ONLINE=120  espera ese tanto a que la placa aparezca (default 15)
#   SKIP_ALARMAS=1   no dispara alarmas (útil si la sirena molesta)
#   CON_AUTOTEST=1   incluye t:test (omitido por defecto)
#   CON_RESTART=1    reinicia la placa al final
#
set -uo pipefail

HOST="${HOST:-cpssecurity.com.ar}"
PORT=8883
PANEL="${TEST_USER:-AV-240AC4000110}"
APP_DIR="/home/servidorcps/SistemaCPS/gateway-to-device"
GW_USER="gateway"
GW_PASS="$(grep '^GTD_MQTT_PASSWORD=' "$APP_DIR/.env" | cut -d= -f2-)"
RUN="$(date +%s)"

OK=0; FAIL=0; SKIP=0; FALLIDOS=()
c_ok()   { echo -e "  \033[32m✓\033[0m $*"; OK=$((OK+1)); }
c_bad()  { echo -e "  \033[31m✗\033[0m $*"; FAIL=$((FAIL+1)); FALLIDOS+=("$*"); }
c_skip() { echo -e "  \033[33m—\033[0m $* (omitido)"; SKIP=$((SKIP+1)); }
titulo() { echo -e "\n\033[1;36m── $* \033[0m"; }

# La captura ve la cfg del panel, que lleva passwords de WiFi: 0600 y se borra.
CAP="$(mktemp)"; chmod 600 "$CAP"
limpiar() { pkill -P $$ mosquitto_sub 2>/dev/null; rm -f "$CAP"; }
trap limpiar EXIT

sub_activo() {
  mosquitto_sub -h "$HOST" -p "$PORT" -u "$GW_USER" -P "$GW_PASS" \
    -t "av/${PANEL}/up" -t "av/${PANEL}/status" -t "av/${PANEL}/tele" \
    -v -i "gtd-livetest-$RUN" >> "$CAP" 2>/dev/null &
  sleep 3
}

# espera <patrón-egrep> <segundos>
espera() {
  local pat="$1" n="${2:-15}"
  for _ in $(seq "$n"); do
    grep -qE "$pat" "$CAP" && return 0
    sleep 1
  done
  return 1
}

# cmd <nombre> <json-sin-cid> <patrón-esperado> [segundos]
cmd() {
  local nombre="$1" cuerpo="$2" espero="$3" secs="${4:-15}"
  local cid="c${RUN}-$((OK+FAIL+SKIP))"
  local payload="${cuerpo/\{/\{\"cid\":\"$cid\",}"
  timeout 10 mosquitto_pub -h "$HOST" -p "$PORT" -u "$GW_USER" -P "$GW_PASS" \
    -t "av/${PANEL}/cmd" -m "$payload" 2>/dev/null \
    || { c_bad "$nombre (no se pudo publicar el cmd)"; return; }
  if espera "${espero//CID/$cid}" "$secs"; then
    c_ok "$nombre"
  else
    c_bad "$nombre — la placa no respondió como se esperaba"
  fi
}

echo "════════════════════════════════════════════════════════"
echo "  Test de la PLACA — camino de bajada"
echo "  panel: $PANEL"
echo "════════════════════════════════════════════════════════"

# ── 0. ¿Está viva? ──────────────────────────────────────────────────
titulo "Presencia de la placa"
sub_activo
ESPERA="${WAIT_ONLINE:-15}"
echo "  (esperando hasta ${ESPERA}s a que publique 'online' — WAIT_ONLINE=120 para"
echo "   arrancar el test y encender la placa después)"
# El status es retained: si la placa está caída, el broker entrega su LWT. Se
# ignora ese offline y se espera un online de verdad.
if espera '"estado":"online"' "$ESPERA"; then
  c_ok "la placa está conectada y publicó su status"
else
  echo
  if grep -q '"estado":"offline"' "$CAP"; then
    echo "  El broker tiene un status RETENIDO de la placa que dice offline (LWT):"
    echo "  la última vez que supo de ella fue para despedirla. No está conectada."
  else
    echo "  La placa no publicó nada en ${ESPERA}s."
  fi
  echo
  echo "  En la consola de la placa tiene que aparecer:  MQTT_T: conectado al broker"
  echo "  Si no llega ahí, el problema es anterior (WiFi / NTP / TLS)."
  echo "  Reintentar con:  WAIT_ONLINE=120 bash deploy/test-panel-live.sh"
  exit 1
fi

# ── 1. Comandos informativos ────────────────────────────────────────
titulo "Comandos que no cambian nada"
cmd "t:estado — reporta su estado"        '{"t":"estado"}'    '"cid":"CID"'
cmd "t:refresh — reenvía su snapshot"     '{"t":"refresh"}'   '"cid":"CID"'
cmd "t:hora — sincroniza reloj"           '{"t":"hora"}'      '"cid":"CID"'
cmd "t:scan — escanea redes WiFi"         '{"t":"scan"}'      '"cid":"CID"' 25
cmd "t:i2c_scan — barre el bus I2C"       '{"t":"i2c_scan"}'  '"cid":"CID"'
if [ "${CON_AUTOTEST:-0}" = "1" ]; then
  cmd "t:test — autotest"                 '{"t":"test"}'      '"cid":"CID"' 25
else
  c_skip "t:test — autotest (CON_AUTOTEST=1 para incluirlo)"
fi
cmd "t:rf op:query — consulta base RF"    '{"t":"rf","op":"query"}' '"cid":"CID"'
cmd "t:cal — calibración"                 '{"t":"cal"}'       '"cid":"CID"'
cmd "t:red — parámetros de red"           '{"t":"red"}'       '"cid":"CID"'

# ── 2. Alarmas de verdad ────────────────────────────────────────────
if [ "${SKIP_ALARMAS:-0}" = "1" ]; then
  titulo "Alarmas"
  c_skip "disparo de alarmas (SKIP_ALARMAS=1)"
else
  titulo "Alarmas — la placa tiene que CAMBIAR de modo y confirmarlo"
  echo "  (puede sonar la sirena; termina siempre en 'off')"
  for MODO in suspicious alert emergency fire medical silent panic off; do
    cmd "alarma → $MODO" \
        '{"t":"alarma","mode":"'"$MODO"'"}' \
        '"t":"alarma".*"mode":"'"$MODO"'".*"origin":"mqtt"' 20
  done

  # Lo que confirma la correlación del diseño: la alarma que sube trae el cid del
  # comando que la causó, y por eso el GtD marca el comando como confirmado.
  titulo "Correlación comando ↔ confirmación"
  CID="corr-${RUN}"
  timeout 10 mosquitto_pub -h "$HOST" -p "$PORT" -u "$GW_USER" -P "$GW_PASS" \
    -t "av/${PANEL}/cmd" -m '{"t":"alarma","cid":"'"$CID"'","mode":"alert"}' 2>/dev/null
  if espera "\"cid\":\"$CID\"" 20; then
    c_ok "la alarma que sube trae el cid del comando que la disparó"
    sleep 2
    if journalctl -u gateway-to-device --since "2 minutes ago" --no-pager -o cat \
         2>/dev/null | grep -q "command confirmado cid=$CID"; then
      c_ok "el GtD marcó el comando como confirmado"
    else
      c_bad "el GtD no registró la confirmación del comando"
    fi
  else
    c_bad "la alarma no volvió con el cid del comando"
  fi
  timeout 10 mosquitto_pub -h "$HOST" -p "$PORT" -u "$GW_USER" -P "$GW_PASS" \
    -t "av/${PANEL}/cmd" -m '{"t":"alarma","cid":"fin-'"$RUN"'","mode":"off"}' 2>/dev/null
  sleep 3
fi

# ── 3. Configuración ────────────────────────────────────────────────
# Se le devuelve SU PROPIA config con cfg_v+1. Mandar una cfg parcial sería
# peligroso: es "estado deseado COMPLETO", y podría borrarle las redes WiFi y
# dejar la placa sin conexión hasta reprovisionarla por el AP.
titulo "Configuración (cfg)"
timeout 10 mosquitto_pub -h "$HOST" -p "$PORT" -u "$GW_USER" -P "$GW_PASS" \
  -t "av/${PANEL}/cmd" -m '{"t":"refresh","cid":"cfg-'"$RUN"'"}' 2>/dev/null
sleep 5
CFG_LINE="$(grep -o '{.*"t":"cfg_full".*}' "$CAP" | tail -1)"
if [ -z "$CFG_LINE" ]; then
  c_skip "prueba de cfg — no se capturó el cfg_full de la placa; mandar una cfg a ciegas podría borrarle las redes WiFi"
else
  NUEVA="$(CFG="$CFG_LINE" python3 - <<'PY'
import json, os
d = json.loads(os.environ["CFG"])
d.pop("t", None); d.pop("v", None); d.pop("ts", None); d.pop("tsq", None)
d["cfg_v"] = int(d.get("cfg_v", 0)) + 1
print(json.dumps(d, separators=(",", ":")))
PY
)"
  NUEVA_V="$(echo "$NUEVA" | python3 -c 'import json,sys; print(json.load(sys.stdin)["cfg_v"])')"
  echo "  (devolviendo su propia config con cfg_v=${NUEVA_V})"
  timeout 10 mosquitto_pub -h "$HOST" -p "$PORT" -u "$GW_USER" -P "$GW_PASS" \
    -t "av/${PANEL}/cfg" -r -m "$NUEVA" 2>/dev/null
  if espera "\"cfg_v\":${NUEVA_V}" 25; then
    c_ok "la placa aplicó la cfg nueva y reportó cfg_v=${NUEVA_V}"
  else
    c_bad "la placa no confirmó la cfg nueva (cfg_v=${NUEVA_V})"
  fi
fi

# ── 4. Restart (opt-in) ─────────────────────────────────────────────
titulo "Reinicio"
if [ "${CON_RESTART:-0}" = "1" ]; then
  timeout 10 mosquitto_pub -h "$HOST" -p "$PORT" -u "$GW_USER" -P "$GW_PASS" \
    -t "av/${PANEL}/cmd" -m '{"t":"restart","cid":"rst-'"$RUN"'"}' 2>/dev/null
  echo "  (esperando a que se vaya y vuelva, hasta 90s)"
  sleep 20
  : > "$CAP"
  espera '"estado":"online"' 70 && c_ok "la placa reinició y volvió sola" \
                                || c_bad "la placa no volvió tras el restart"
else
  c_skip "restart (CON_RESTART=1 para incluirlo)"
fi

# ── Resumen ─────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════"
if [ "$FAIL" -eq 0 ]; then
  echo -e "  \033[32mTODO OK\033[0m — $OK verificaciones, $SKIP omitidas"
else
  echo -e "  \033[31m$FAIL FALLARON\033[0m de $((OK+FAIL)) — $SKIP omitidas"
  for f in "${FALLIDOS[@]}"; do echo "    · $f"; done
  echo
  echo "  Un comando sin respuesta puede ser que el firmware no lo implemente"
  echo "  todavía, no necesariamente un error. Ver el detalle con:"
  echo "      journalctl -u gateway-to-device -n 60"
fi
echo "════════════════════════════════════════════════════════"
echo
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
