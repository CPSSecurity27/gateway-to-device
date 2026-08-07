#!/usr/bin/env bash
# Corte del broker-bridge al puente nuevo (cps-legacy-app).
#
# Idempotente. Se puede repetir. Rollback al final del archivo.
set -euo pipefail
BASE=/home/servidorcps/SistemaCPS/gateway-to-device
DB=cpssecurityarg
ok(){ echo "  [ok] $*"; }
paso(){ echo; echo "== $* =="; }

[[ $EUID -eq 0 ]] || { echo "Corré esto con sudo."; exit 1; }
CLAVE=$(cat /home/servidorcps/.cps_legacy_pass)

paso "1. Rol cps_legacy"
sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='cps_legacy') THEN
    CREATE ROLE cps_legacy LOGIN PASSWORD '${CLAVE}';
  ELSE
    ALTER ROLE cps_legacy PASSWORD '${CLAVE}';
  END IF;
END \$\$;
GRANT CONNECT ON DATABASE ${DB} TO cps_legacy;
SQL
ok "rol creado/actualizado"

paso "2. GRANTs — una funcion de subida, tres de lectura, nada mas"
sudo -u postgres psql -d "$DB" -v ON_ERROR_STOP=1 -q <<'SQL'
GRANT USAGE ON SCHEMA gtd TO cps_legacy;
REVOKE ALL ON ALL TABLES IN SCHEMA gtd FROM cps_legacy;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA gtd FROM cps_legacy;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM cps_legacy;
GRANT USAGE ON SCHEMA public TO cps_legacy;
GRANT EXECUTE ON FUNCTION
  gtd.enqueue_legacy_alarm(TEXT, TEXT, DOUBLE PRECISION, DOUBLE PRECISION),
  gtd.legacy_snapshot(INT), gtd.legacy_devices(), gtd.legacy_clientes()
TO cps_legacy;
SQL
ok "no puede leer device ni resolver eventos por su cuenta"

paso "3. Unidad de systemd"
install -m 0644 "$BASE/deploy/cps-legacy-app.service" /etc/systemd/system/
systemctl daemon-reload
ok "cps-legacy-app.service instalada"

paso "4. Apagar el broker-bridge"
systemctl disable --now broker-bridge || true
ok "broker-bridge detenido y deshabilitado"

paso "5. Levantar el puente"
systemctl enable --now cps-legacy-app
sleep 6

paso "6. Verificacion"
systemctl is-active --quiet cps-legacy-app || { journalctl -u cps-legacy-app -n 30 --no-pager; echo; echo "FALLO: el puente no levanto."; exit 1; }
ok "cps-legacy-app ACTIVO"
systemctl is-active --quiet broker-bridge && { echo "FALLO: el bridge sigue vivo, estarian duplicados."; exit 1; }
ok "broker-bridge apagado"
systemctl is-active --quiet gateway-to-device || echo "  [!] OJO: el GtD no esta activo, los comandos no van a salir"
ok "GtD activo"
echo
journalctl -u cps-legacy-app -n 15 --no-pager

cat <<'FIN'

  ROLLBACK (si algo sale mal):
    sudo systemctl disable --now cps-legacy-app
    sudo systemctl enable --now broker-bridge
FIN
