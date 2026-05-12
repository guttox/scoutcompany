#!/usr/bin/env bash
# Configura webhook MESSAGES_UPSERT na Evolution apontando pro scout-app interno.
set -e

INSTALL_DIR="/opt/scout"
cd "$INSTALL_DIR"
set -a; . ./.env; set +a

API="http://localhost:8080"
KEY="${EVOLUTION_APIKEY:?EVOLUTION_APIKEY não definida}"
INST="${EVOLUTION_INSTANCE:-scout-wa}"

# Dentro da rede do compose, scout-app é alcançado pelo hostname "scout"
WEBHOOK_URL="http://scout:5005/webhook/whatsapp"

echo "→ Configurando webhook em Evolution: $WEBHOOK_URL"
curl -s -X POST "$API/webhook/set/$INST" \
    -H "apikey: $KEY" \
    -H "Content-Type: application/json" \
    -d "{
        \"webhook\": {
            \"enabled\": true,
            \"url\": \"$WEBHOOK_URL\",
            \"events\": [\"MESSAGES_UPSERT\"],
            \"webhookByEvents\": false,
            \"webhookBase64\": false
        }
    }"
echo ""
echo "→ Confirmando…"
curl -s "$API/webhook/find/$INST" -H "apikey: $KEY"
echo ""
echo "✅ Webhook configurado."
