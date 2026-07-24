#!/usr/bin/env bash
#
# Test de punta a punta contra el sistema DESPLEGADO.
#
#   bash deploy/test-e2e.sh          (no necesita sudo)
#
# Se hace pasar por un panel real: publica por 8883 con TLS y credencial, y
# verifica en el journal qué hizo el GtD con cada mensaje. Cubre el catálogo
# entero de la alarma —los 8 modos, los 4 orígenes, los 5 tipos de `up`, los 3
# estados de presencia— más los casos rotos y el aislamiento de la ACL.
#
# Lo que los tests unitarios NO pueden probar y esto sí: TLS, credenciales, ACL,
# ruteo del broker, systemd, y que el servicio siga vivo después de todo.
#
# Deja estado sintético en el panel_state en memoria del GtD (se limpia solo al
# reiniciar el servicio, o cuando el panel real vuelva a publicar).

set -uo pipefail

HOST="${HOST:-cpssecurity.com.ar}"
PORT=8883
USER_PANEL="${TEST_USER:-AV-240AC4000110}"
PASS_PANEL="${TEST_PASS:-dev-sin-secreto}"
UNIT="gateway-to-device"
RUN="$(date +%s)"          # marca única por corrida: no colisiona con la anterior

OK=0; FAIL=0; FALLIDOS=()

c_ok()   { echo -e "  \033[32m✓\033[0m $*"; OK=$((OK+1)); }
c_bad()  { echo -e "  \033[31m✗\033[0m $*"; FAIL=$((FAIL+1)); FALLIDOS+=("$*"); }
titulo() { echo -e "\n\033[1;36m── $* \033[0m"; }

pub() {  # pub <canal> <payload>  — publica como el panel
  timeout 10 mosquitto_pub -h "$HOST" -p "$PORT" -u "$USER_PANEL" -P "$PASS_PANEL" \
    -t "av/${USER_PANEL}/$1" -m "$2" 2>/dev/null
}

# espera <patrón> — busca en el journal desde $SINCE, hasta 6s
espera() {
  for _ in 1 2 3 4 5 6; do
    if journalctl -u "$UNIT" --since "$SINCE" --no-pager -o cat 2>/dev/null | grep -q "$1"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# `journalctl --since` tiene granularidad de UN SEGUNDO y es inclusivo, así que
# una marca tomada en el mismo segundo que la línea anterior la vuelve a contar.
# Se arranca la ventana un segundo en el futuro y se espera a que pase.
marca() { SINCE="$(date -d '+1 second' '+%Y-%m-%d %H:%M:%S')"; sleep 2; }

# caso <nombre> <canal> <payload> <patrón esperado>
caso() {
  marca
  pub "$2" "$3" || { c_bad "$1 (no se pudo publicar)"; return; }
  espera "$4" && c_ok "$1" || c_bad "$1 — no apareció: $4"
}

# caso_no <nombre> <canal> <payload> <patrón que NO debe aparecer>
caso_no() {
  marca
  pub "$2" "$3" >/dev/null 2>&1 || true
  sleep 3
  if journalctl -u "$UNIT" --since "$SINCE" --no-pager -o cat 2>/dev/null | grep -q "$4"; then
    c_bad "$1 — apareció lo que NO debía: $4"
  else
    c_ok "$1"
  fi
}

echo "════════════════════════════════════════════════════════"
echo "  Test de punta a punta — GtD"
echo "  panel: $USER_PANEL   broker: ${HOST}:${PORT}"
echo "════════════════════════════════════════════════════════"

systemctl is-active --quiet "$UNIT" || { echo "El servicio $UNIT no está activo."; exit 1; }
REINICIOS_ANTES="$(systemctl show "$UNIT" -p NRestarts --value)"
INICIO="$(date '+%Y-%m-%d %H:%M:%S')"   # ventana de toda la corrida

# ── Presencia ───────────────────────────────────────────────────────
titulo "Presencia (los 3 estados del contrato)"
caso "status online" status \
  '{"v":1,"estado":"online","modo":"ACTIVE_240","fw":"e2e","ts":'"$RUN"'}' \
  "panel ONLINE"

# El status es retained: el broker lo reenvía en cada reconexión. Una sola línea.
# Se cuenta sobre TODA la corrida en vez de una ventana corta: así la medición no
# depende de la granularidad de un segundo del journal.
pub status '{"v":1,"estado":"online","modo":"ACTIVE_240","fw":"e2e","ts":'"$((RUN+1))"'}'
sleep 3
N="$(journalctl -u "$UNIT" --since "$INICIO" --no-pager -o cat | grep -c "panel ONLINE")"
[ "$N" -eq 1 ] && c_ok "status repetido no repite el log (1 sola línea para 2 mensajes)" \
                || c_bad "se esperaba 1 línea 'panel ONLINE' en la corrida, hubo $N"

caso "status durmiendo" status \
  '{"v":1,"estado":"durmiendo","despierta":'"$((RUN+3600))"',"ts":'"$RUN"'}' \
  "panel DURMIENDO"
caso "status offline (LWT)" status \
  '{"v":1,"estado":"offline","causa":"lwt","ts":'"$RUN"'}' \
  "panel OFFLINE"

# ── Telemetría ──────────────────────────────────────────────────────
titulo "Telemetría"
caso "tele con energía y modo de alarma" tele \
  '{"v":1,"cfg_v":3,"rf_gen":2,"alarma":{"mode":"off"},"energia":{"modo":"ACTIVE_240","vbat":12.6,"vpanel":21.4,"vfuente":34.9},"ts":'"$RUN"'}' \
  "vbat.*12.6"

# ── Alarmas: el catálogo completo ───────────────────────────────────
titulo "Alarmas — los 8 modos"
for MODO in off suspicious alert emergency fire medical silent panic; do
  caso "alarma $MODO" up \
    '{"v":1,"t":"alarma","eid":"'"$RUN"'-'"$MODO"'","mode":"'"$MODO"'","prev":"off","origin":"rf","dni":12345678,"ts":'"$RUN"'}' \
    "eid=${RUN}-${MODO}"
done

titulo "Alarmas — los 4 orígenes"
for ORIGEN in rf mqtt auto portal; do
  caso "origen $ORIGEN" up \
    '{"v":1,"t":"alarma","eid":"'"$RUN"'-or-'"$ORIGEN"'","mode":"alert","prev":"off","origin":"'"$ORIGEN"'","ts":'"$RUN"'}' \
    "eid=${RUN}-or-${ORIGEN}"
done

titulo "Idempotencia y correlación"
caso_no "el mismo eid dos veces no duplica" up \
  '{"v":1,"t":"alarma","eid":"'"$RUN"'-off","mode":"off","prev":"off","origin":"rf","ts":'"$RUN"'}' \
  "evento\[.*\] tipo=alarma eid=${RUN}-off$"

caso "alarma por MQTT confirma su comando (cid)" up \
  '{"v":1,"t":"alarma","eid":"'"$RUN"'-cid","mode":"emergency","prev":"off","origin":"mqtt","cid":"cmd-'"$RUN"'","ts":'"$RUN"'}' \
  "command confirmado cid=cmd-${RUN}"

caso "ack cierra el ciclo del downlink" up \
  '{"v":1,"t":"ack","cid":"ack-'"$RUN"'","res":"ok","det":"listo","ts":'"$RUN"'}' \
  "command confirmado cid=ack-${RUN}"

# ── Resto del canal up ──────────────────────────────────────────────
titulo "Otros tipos de up"
caso "scan de redes WiFi" up \
  '{"v":1,"t":"scan","redes":[{"ssid":"E2E","rssi":-60}],"ts":'"$RUN"'}' \
  "tipo=scan"
caso "progreso de OTA" up \
  '{"v":1,"t":"ota","estado":2,"fw":"6.0.0","ts":'"$RUN"'}' \
  "tipo=ota"
caso "cfg_full va al espejo de config" up \
  '{"v":1,"t":"cfg_full","cfg_v":'"$((RUN % 1000))"',"redes":[{"ssid":"E2E","psw":"secreto-e2e"}],"id":{"dev":"'"$USER_PANEL"'","fw":"e2e"},"ts":'"$RUN"'}' \
  "config espejo.*cfg_v=$((RUN % 1000))"

titulo "Secretos"
marca
sleep 2
if journalctl -u "$UNIT" --since "$(date -d '10 minutes ago' '+%Y-%m-%d %H:%M:%S')" \
     --no-pager -o cat 2>/dev/null | grep -q "secreto-e2e"; then
  c_bad "LA PASSWORD DE WIFI APARECIÓ EN EL LOG"
else
  c_ok "la password de la cfg no llegó al log"
fi

# ── Payloads rotos ──────────────────────────────────────────────────
titulo "Robustez (nada de esto puede tirar el servicio)"
caso "JSON inválido se descarta" up 'no soy json {' "descartado"
caso "esquema incompatible se descarta" up '{"v":99,"t":"alarma","eid":"x"}' "descartado"
caso "t desconocido se descarta" up '{"v":1,"t":"marciano","ts":1}' "descartado"
caso "alarma sin eid se descarta" up \
  '{"v":1,"t":"alarma","mode":"off","prev":"off","origin":"rf","ts":1}' "descartado"

# ── Aislamiento de la ACL ───────────────────────────────────────────
titulo "Aislamiento entre paneles (ACL del broker)"
marca
timeout 10 mosquitto_pub -h "$HOST" -p "$PORT" -u "$USER_PANEL" -P "$PASS_PANEL" \
  -t "av/AV-DEADBEEF0000/status" \
  -m '{"v":1,"estado":"online","modo":"INTRUSION-'"$RUN"'","ts":1}' 2>/dev/null || true
sleep 3
if journalctl -u "$UNIT" --since "$SINCE" --no-pager -o cat | grep -q "INTRUSION-${RUN}"; then
  c_bad "FALLA DE AISLAMIENTO: el panel escribió en los tópicos de otro"
else
  c_ok "un panel no puede publicar en los tópicos de otro"
fi

marca
if timeout 10 mosquitto_pub -h "$HOST" -p "$PORT" -u "$USER_PANEL" -P "mal-password" \
     -t "av/${USER_PANEL}/status" -m '{"v":1,"estado":"online"}' 2>/dev/null; then
  c_bad "el broker aceptó una password incorrecta"
else
  c_ok "el broker rechaza una password incorrecta"
fi

# ── El servicio sobrevivió ──────────────────────────────────────────
titulo "Estado final del servicio"
systemctl is-active --quiet "$UNIT" && c_ok "el GtD sigue activo" \
                                    || c_bad "el GtD se cayó durante el test"
REINICIOS_DESPUES="$(systemctl show "$UNIT" -p NRestarts --value)"
[ "$REINICIOS_ANTES" = "$REINICIOS_DESPUES" ] \
  && c_ok "no se reinició (${REINICIOS_DESPUES} reinicios, igual que al empezar)" \
  || c_bad "el servicio se reinició durante el test"

# ── Resumen ─────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════"
if [ "$FAIL" -eq 0 ]; then
  echo -e "  \033[32mTODO OK\033[0m — $OK verificaciones"
else
  echo -e "  \033[31m$FAIL FALLARON\033[0m de $((OK+FAIL))"
  for f in "${FALLIDOS[@]}"; do echo "    · $f"; done
fi
echo "════════════════════════════════════════════════════════"
echo
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
