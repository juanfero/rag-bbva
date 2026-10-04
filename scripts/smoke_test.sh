#!/usr/bin/env bash
# Smoke test del despliegue con Docker (M12): API viva, índice cargado, una pregunta
# respondida con fuentes, historial y UI. Gasta 1 llamada al LLM.
#
# Uso: scripts/smoke_test.sh [URL_API] [URL_UI]
#   (por defecto http://127.0.0.1:${API_PUBLISHED_PORT:-8000} y :${UI_PUBLISHED_PORT:-8501})
set -euo pipefail

API="${1:-http://127.0.0.1:${API_PUBLISHED_PORT:-8000}}"
UI="${2:-http://127.0.0.1:${UI_PUBLISHED_PORT:-8501}}"
ESPERA="${SMOKE_TIMEOUT_SECONDS:-300}"
fallo() { echo "FALLA: $*" >&2; exit 1; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
CHAT=$(mktemp)
trap 'rm -f "$CHAT"' EXIT

echo "== 1. API viva (hasta ${ESPERA} s)"
inicio=$(date +%s)
until curl -s -o /dev/null "$API/health"; do
  (( $(date +%s) - inicio > ESPERA )) && fallo "la API no responde en $API"
  sleep 3
done
salud=$(curl -s "$API/health")
echo "$salud"
[[ $(echo "$salud" | json 'd["qdrant"]["status"]') == ok ]] || fallo "Qdrant no está ok"
puntos=$(echo "$salud" | json 'd["qdrant"]["points"]')
(( puntos > 0 )) || fallo "la colección está vacía"
echo "OK: Qdrant con $puntos puntos"
[[ $(echo "$salud" | json 'd["llm"]["key_configured"]') == True ]] || fallo "falta GEMINI_API_KEY en .env"

echo "== 2. Pregunta con fuentes"
codigo=$(curl -s -o "$CHAT" -w '%{http_code}' -X POST "$API/chat" \
  -H 'Content-Type: application/json' -d '{"question": "¿Qué es un CDT?"}')
[[ $codigo == 200 ]] || fallo "POST /chat respondió $codigo: $(cat "$CHAT")"
fuentes=$(json 'len(d["sources"])' < "$CHAT")
id=$(json 'd["conversation_id"]' < "$CHAT")
echo "respuesta: $(json 'd["answer"][:160]' < "$CHAT")…"
echo "fuentes: $fuentes · modelo: $(json 'd["model"]' < "$CHAT") · total: $(json 'd["timings"]["total"]' < "$CHAT") ms"
(( fuentes > 0 )) || fallo "la respuesta no trae fuentes"

echo "== 3. Historial"
curl -s "$API/conversations/$id/messages" | json 'len(d["messages"])' | grep -qx 2 \
  || fallo "la conversación $id no tiene sus 2 mensajes"
echo "OK: conversación $id guardada"

echo "== 4. Interfaz"
[[ $(curl -s "$UI/_stcore/health") == ok ]] || fallo "la UI no responde en $UI"
echo "OK: UI en $UI"
echo "SMOKE TEST: OK"
