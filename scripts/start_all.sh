#!/usr/bin/env bash
# Inicializa o ecossistema completo do Scout:
#   1. Confirma Evolution API rodando em :8080
#   2. Sobe webhook_server.py em :5005 (background, se não estiver rodando)
#   3. Configura webhook na Evolution apontando pra :5005
#   4. Mostra status final
#
# Idempotente: pode rodar várias vezes que detecta o que já está up.

set -u
ROOT="$HOME/scout"
VENV="$ROOT/venv"
LOG_DIR="$ROOT/logs"
PID_DIR="$ROOT/.pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

EVOLUTION_URL="${EVOLUTION_URL:-http://localhost:8080}"
EVOLUTION_APIKEY="${EVOLUTION_APIKEY:-scout-evolution-key}"
EVOLUTION_INSTANCE="${EVOLUTION_INSTANCE:-scout-wa}"
WEBHOOK_PORT="${WEBHOOK_PORT:-5005}"
WEBHOOK_HOST_URL="http://host.docker.internal:${WEBHOOK_PORT}/webhook/whatsapp"

echo "═══════════════════════════════════════"
echo "  SCOUT — BOOTSTRAP"
echo "═══════════════════════════════════════"
echo "Evolution: $EVOLUTION_URL · instância: $EVOLUTION_INSTANCE"
echo "Webhook:   :${WEBHOOK_PORT} (URL Evolution-side: $WEBHOOK_HOST_URL)"
echo ""

# ── 1. Evolution rodando? ───────────────────────────────
echo "→ [1/4] Verifica Evolution API"
if ! curl -sf "$EVOLUTION_URL/" -o /dev/null; then
  echo "  ✗ Evolution API não responde em $EVOLUTION_URL"
  echo "    Suba com: cd ~/scout/evolution && docker compose up -d"
  exit 1
fi
EVO_VERSION=$(curl -s "$EVOLUTION_URL/" | "$VENV/bin/python" -c "import json,sys;print(json.load(sys.stdin).get('version','?'))" 2>/dev/null || echo "?")
echo "  ✓ Evolution v$EVO_VERSION respondendo"

# Estado da instância
STATE=$(curl -s "$EVOLUTION_URL/instance/connectionState/$EVOLUTION_INSTANCE" \
         -H "apikey: $EVOLUTION_APIKEY" \
         | "$VENV/bin/python" -c "import json,sys;d=json.load(sys.stdin);print(d.get('instance',{}).get('state','?'))" 2>/dev/null || echo "?")
echo "  ✓ Instância $EVOLUTION_INSTANCE: $STATE"
if [ "$STATE" != "open" ]; then
  echo "  ⚠️  Instância não está aberta. Vá em ~/scout/qrcode.png e escaneie."
fi

# ── 2. Webhook server rodando? ──────────────────────────
echo ""
echo "→ [2/4] Verifica webhook server"
HEALTH=$(curl -sf "http://localhost:$WEBHOOK_PORT/health" 2>/dev/null || echo "")
if [ -n "$HEALTH" ]; then
  echo "  ✓ Webhook já está up em :$WEBHOOK_PORT"
else
  echo "  → Subindo webhook_server.py em background"
  nohup "$VENV/bin/python" "$ROOT/scripts/webhook_server.py" \
        > "$LOG_DIR/webhook.log" 2>&1 &
  echo $! > "$PID_DIR/webhook.pid"
  sleep 2
  HEALTH=$(curl -sf "http://localhost:$WEBHOOK_PORT/health" 2>/dev/null || echo "")
  if [ -z "$HEALTH" ]; then
    echo "  ✗ Webhook não subiu — veja $LOG_DIR/webhook.log"
    exit 1
  fi
  echo "  ✓ Webhook subiu (pid $(cat "$PID_DIR/webhook.pid"))"
fi

# ── 3. Configura webhook na Evolution ───────────────────
echo ""
echo "→ [3/4] Configura webhook na Evolution"
WEBHOOK_BODY=$(cat <<EOF
{
  "webhook": {
    "enabled": true,
    "url": "$WEBHOOK_HOST_URL",
    "events": ["MESSAGES_UPSERT"],
    "webhookByEvents": false,
    "webhookBase64": false
  }
}
EOF
)
SET_RESP=$(curl -s -X POST "$EVOLUTION_URL/webhook/set/$EVOLUTION_INSTANCE" \
  -H "apikey: $EVOLUTION_APIKEY" \
  -H "Content-Type: application/json" \
  -d "$WEBHOOK_BODY")
echo "  ✓ webhook/set respondeu: ${SET_RESP:0:120}"

# Confirma
FIND_RESP=$(curl -s "$EVOLUTION_URL/webhook/find/$EVOLUTION_INSTANCE" \
  -H "apikey: $EVOLUTION_APIKEY")
echo "  ✓ webhook/find: ${FIND_RESP:0:200}"

# ── 4. Status final ─────────────────────────────────────
echo ""
echo "→ [4/4] Status final"
echo "  Evolution:    $EVO_VERSION · estado=$STATE"
echo "  Webhook:      $(curl -s "http://localhost:$WEBHOOK_PORT/health")"
echo "  DRY_RUN:      $(grep -E '^SCOUT_DRY_RUN=' "$ROOT/.env" | cut -d= -f2)"
echo ""
echo "Logs:"
echo "  tail -f $LOG_DIR/webhook.log"
echo "  tail -f $LOG_DIR/disparos.log"
echo ""
echo "Pra testar manualmente:"
echo "  Envie uma mensagem para 5511940670464 do seu celular"
echo "  E observe o log: tail -f $LOG_DIR/webhook.log"
echo ""
echo "═══════════════════════════════════════"
echo "  ✅ SCOUT — PRONTO"
echo "═══════════════════════════════════════"
