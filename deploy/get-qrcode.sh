#!/usr/bin/env bash
# Cria instância scout-wa e baixa o QR code pra reconectar WhatsApp.
# Roda na VPS depois do install.sh.
set -e

INSTALL_DIR="/opt/scout"
cd "$INSTALL_DIR"

# Carrega .env pra pegar EVOLUTION_APIKEY
set -a
. ./.env
set +a

API="http://localhost:8080"
KEY="${EVOLUTION_APIKEY:?EVOLUTION_APIKEY não definida no .env}"
INST="${EVOLUTION_INSTANCE:-scout-wa}"

echo "→ Apagando instância antiga (se existir)…"
curl -s -X DELETE "$API/instance/delete/$INST" -H "apikey: $KEY" > /dev/null || true
sleep 2

echo "→ Criando instância $INST…"
curl -s -X POST "$API/instance/create" \
    -H "apikey: $KEY" \
    -H "Content-Type: application/json" \
    -d "{\"instanceName\":\"$INST\",\"qrcode\":true,\"integration\":\"WHATSAPP-BAILEYS\"}" > /tmp/scout-create.json
sleep 2

echo "→ Buscando QR base64…"
for i in 1 2 3 4 5 6 7 8 9 10; do
    curl -s "$API/instance/connect/$INST" -H "apikey: $KEY" > /tmp/scout-qr.json
    B64=$(python3 -c "import json; d=json.load(open('/tmp/scout-qr.json')); print(d.get('base64','') or '')")
    if [ -n "$B64" ]; then
        # remove header data:image/png;base64,
        echo "$B64" | sed 's|^data:image/png;base64,||' | base64 -d > /opt/scout/qrcode.png
        echo "✅ QR salvo em /opt/scout/qrcode.png"
        echo ""
        echo "Pra ver o QR:"
        echo "  • SSH com forwarding:    scp user@vps:/opt/scout/qrcode.png . && open qrcode.png"
        echo "  • Ou suba pra web (CUIDADO — link público temporário):"
        echo "      python3 -m http.server 8000 --directory /opt/scout"
        echo "      depois: http://IP_DA_VPS:8000/qrcode.png"
        echo ""
        echo "Escaneie no WhatsApp Business: Menu → Aparelhos conectados → Conectar aparelho"
        exit 0
    fi
    echo "  [$i] aguardando QR…"
    sleep 2
done
echo "✗ QR não foi gerado em 20s. Confira docker compose logs evolution"
exit 1
