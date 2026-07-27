#!/usr/bin/env bash
#
# Diagnóstico: ¿por qué el salt no reproduce el vector de verificación?
#
#   bash deploy/diag-salt.sh
#
# Prueba una batería de variantes plausibles del algoritmo con el salt que se
# ingresa, y dice cuál (si alguna) reproduce el vector publicado. Sirve para
# separar dos causas que se ven igual:
#
#   a) el salt no es el de producción      → ninguna variante coincide
#   b) el algoritmo del doc no es el que    → coincide una variante distinta de
#      se usó para generar el vector           la canónica, y dice cuál
#
# NO registra nada, NO escribe en el broker, NO imprime el salt ni las passwords
# derivadas: solo dice qué variante coincide.

set -uo pipefail

VECTOR_MAC="A842E38FCA6C"
VECTOR_PASS="4EA453D76DD9E1C81A0D141B"

echo "════════════════════════════════════════════════════════"
echo "  Diagnóstico del salt de derivación"
echo "  vector: MAC ${VECTOR_MAC} → ${VECTOR_PASS}"
echo "════════════════════════════════════════════════════════"
echo

SALT="${MQTT_DERIV_SALT:-${SALT_MQTT:-}}"
if [ -z "$SALT" ]; then
  echo -n "SALT a diagnosticar (no se muestra al tipear): "
  read -rs SALT
  echo
fi
[ -n "$SALT" ] || { echo "Salt vacío."; exit 1; }

SALT="$SALT" MAC="$VECTOR_MAC" ESPERADO="$VECTOR_PASS" python3 - <<'PYEOF'
import hashlib, hmac, os

salt_str = os.environ["SALT"]
hexmac   = os.environ["MAC"]
esperado = os.environ["ESPERADO"]

salt = salt_str.encode()
mac6 = bytes.fromhex(hexmac)

def H(key, msg, alg=hashlib.sha256):
    return hmac.new(key, msg, alg).digest()

variantes = {
    # La del doc, tal cual está escrita.
    "CANÓNICA  hmac(salt, mac6)[:12] hex mayús":
        H(salt, mac6)[:12].hex().upper(),

    # Confusiones de implementación frecuentes
    "hmac(salt, mac6)[:12] hex MINÚS":
        H(salt, mac6)[:12].hex(),
    "hmac(salt, mac6)[-12:] (últimos 12 bytes)":
        H(salt, mac6)[-12:].hex().upper(),
    "hmac(salt, mac6)[:16] (16 bytes)":
        H(salt, mac6)[:16].hex().upper(),
    "hmac(mac6, salt)  <- key y msg invertidos":
        H(mac6, salt)[:12].hex().upper(),

    # El msg como STRING en vez de bytes crudos
    "msg = 'A842E38FCA6C' (string hex mayús)":
        H(salt, hexmac.encode())[:12].hex().upper(),
    "msg = 'a842e38fca6c' (string hex minús)":
        H(salt, hexmac.lower().encode())[:12].hex().upper(),
    "msg = 'AV-A842E38FCA6C' (el id completo)":
        H(salt, f"AV-{hexmac}".encode())[:12].hex().upper(),
    "msg = 'A8:42:E3:8F:CA:6C' (con dos puntos)":
        H(salt, ":".join(hexmac[i:i+2] for i in range(0, 12, 2)).encode())[:12].hex().upper(),

    # El salt con basura al final (copiar/pegar, editores, C con el NUL)
    "key = salt + '\\0' (el NUL de C)":
        H(salt + b"\x00", mac6)[:12].hex().upper(),
    "key = salt + '\\n'":
        H(salt + b"\n", mac6)[:12].hex().upper(),
    "key = salt.strip() (sin espacios alrededor)":
        H(salt_str.strip().encode(), mac6)[:12].hex().upper(),

    # El salt interpretado como hex en vez de texto
    "key = bytes.fromhex(salt)":
        (H(bytes.fromhex(salt_str), mac6)[:12].hex().upper()
         if all(c in "0123456789abcdefABCDEF" for c in salt_str) and len(salt_str) % 2 == 0
         else None),

    # Otros algoritmos
    "HMAC-SHA1 en vez de SHA256":
        H(salt, mac6, hashlib.sha1)[:12].hex().upper(),
    "sha256(salt || mac) SIN hmac":
        hashlib.sha256(salt + mac6).digest()[:12].hex().upper(),
    "sha256(mac || salt) SIN hmac":
        hashlib.sha256(mac6 + salt).digest()[:12].hex().upper(),
}

hits = [n for n, v in variantes.items() if v is not None and v == esperado]

for nombre, valor in variantes.items():
    if valor is None:
        print(f"  --         {nombre}  (no aplica)")
    elif valor == esperado:
        print(f"  \033[32m[OK] COINCIDE\033[0m {nombre}")
    else:
        print(f"  [  ]       {nombre}")

print()
print("-" * 56)
if not hits:
    print("""
  NINGUNA variante reproduce el vector.

  La causa más probable es que el salt ingresado NO sea el de producción
  (¿el placeholder del build de laboratorio? ¿un typo? ¿otro entorno?).

  Qué hacer:
    · Confirmar con quien compiló el firmware de producción cuál es el
      SALT_MQTT que quedó inyectado por -D en ese build.
    · Confirmar que el vector del doc se generó con ESE salt y no con uno
      anterior — un vector viejo produce exactamente este síntoma.

  No registres paneles hasta cerrar esto: quedarían con credenciales que el
  firmware no puede reproducir.
""")
elif hits == ["CANÓNICA  hmac(salt, mac6)[:12] hex mayús"]:
    print("\n  El salt y el algoritmo son correctos. (No debería haber fallado.)\n")
else:
    print(f"""
  El salt es CORRECTO, pero el algoritmo del doc no es el que generó el vector.
  La variante que coincide es:

      {hits[0]}

  Hay que alinear docs/02 y deploy/provision-panel.sh con esa forma antes de
  registrar nada, y confirmarla contra el firmware.
""")
PYEOF
