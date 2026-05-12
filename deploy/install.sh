#!/usr/bin/env bash
# Bootstrap do Scout numa VPS Ubuntu/Debian.
#
# Como usar (na VPS, como root ou sudo):
#   curl -fsSL https://raw.githubusercontent.com/guttox/scoutcompany/main/deploy/install.sh | bash
#   OU, depois de clonar manualmente:
#   bash /opt/scout/deploy/install.sh
#
# Idempotente: pode rodar várias vezes — só faz o que falta.

set -e

REPO_URL="https://github.com/guttox/scoutcompany.git"
INSTALL_DIR="/opt/scout"

echo "═══════════════════════════════════════"
echo "  SCOUT — INSTALL VPS"
echo "═══════════════════════════════════════"

# ───────────── 1. Detecta SO + privilégios ─────────────
if [ "$EUID" -ne 0 ]; then
    SUDO="sudo"
else
    SUDO=""
fi
if ! command -v apt-get >/dev/null; then
    echo "✗ Esse script assume Ubuntu/Debian (apt-get). Adapte pra sua distro."
    exit 1
fi

# ───────────── 2. Docker ─────────────
echo ""
echo "→ [1/6] Docker"
if ! command -v docker >/dev/null; then
    echo "  Instalando Docker..."
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq ca-certificates curl gnupg
    $SUDO install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
    OS_ID=$(. /etc/os-release && echo "$ID")
    OS_CODENAME=$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}")
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/$OS_ID $OS_CODENAME stable" \
        | $SUDO tee /etc/apt/sources.list.d/docker.list > /dev/null
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    $SUDO systemctl enable --now docker
    echo "  ✓ Docker instalado"
else
    echo "  ✓ Docker já instalado: $(docker --version)"
fi

# ───────────── 3. Git + clone ─────────────
echo ""
echo "→ [2/6] Repositório"
$SUDO apt-get install -y -qq git
if [ ! -d "$INSTALL_DIR/.git" ]; then
    echo "  Clonando $REPO_URL → $INSTALL_DIR"
    $SUDO git clone "$REPO_URL" "$INSTALL_DIR"
else
    echo "  Repo já existe, atualizando…"
    $SUDO git -C "$INSTALL_DIR" pull --ff-only
fi

# ───────────── 4. .env ─────────────
echo ""
echo "→ [3/6] Variáveis de ambiente"
ENV_FILE="$INSTALL_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    $SUDO cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
    $SUDO chmod 600 "$ENV_FILE"
    echo "  ⚠️  $ENV_FILE criado a partir do template."
    echo "      EDITE AGORA com seus tokens reais antes de subir o stack:"
    echo "        sudo nano $ENV_FILE"
    echo ""
    echo "      Variáveis OBRIGATÓRIAS pra preencher:"
    echo "        ANTHROPIC_API_KEY      (Claude IA)"
    echo "        GOOGLE_PLACES_KEY      (busca prospects)"
    echo "        TELEGRAM_TOKEN         (bot @usescout_bot)"
    echo "        TELEGRAM_CHAT_ID       (seu chat pessoal)"
    echo "        EVOLUTION_APIKEY       (qualquer string forte — você define)"
    echo "        WHATSAPP_SCOUT         (5511940670464)"
    echo "        DISPATCH_MODE          (DRY pra começar, LIVE depois de validar)"
    echo ""
    echo "  Quando terminar de editar, rode esse script de novo."
    exit 0
else
    echo "  ✓ $ENV_FILE já existe"
fi

# ───────────── 5. Sobe stack ─────────────
echo ""
echo "→ [4/6] Subindo stack Docker"
cd "$INSTALL_DIR"
$SUDO docker compose pull
$SUDO docker compose up -d --build
sleep 8

echo ""
echo "→ [5/6] Health checks"
EVOLUTION_OK=$(curl -sf http://localhost:8080/ > /dev/null && echo OK || echo FAIL)
WEBHOOK_OK=$(curl -sf http://localhost:5005/health > /dev/null && echo OK || echo FAIL)
echo "  Evolution :8080  → $EVOLUTION_OK"
echo "  Webhook   :5005  → $WEBHOOK_OK"

# ───────────── 6. Cron ─────────────
echo ""
echo "→ [6/6] Cron jobs"
TARGET_USER="${SUDO_USER:-root}"
if [ "$TARGET_USER" = "root" ]; then
    $SUDO crontab "$INSTALL_DIR/deploy/crontab.scout"
else
    $SUDO -u "$TARGET_USER" crontab "$INSTALL_DIR/deploy/crontab.scout"
fi
echo "  ✓ crontab instalado pra usuário $TARGET_USER"

# ───────────── Final ─────────────
echo ""
echo "═══════════════════════════════════════"
echo "  ✅ SCOUT INSTALADO"
echo "═══════════════════════════════════════"
echo ""
echo "Próximos passos manuais:"
echo ""
echo "1. Criar instância WhatsApp na Evolution:"
echo "   curl -X POST http://localhost:8080/instance/create \\"
echo "     -H \"apikey: \$EVOLUTION_APIKEY\" \\"
echo "     -H \"Content-Type: application/json\" \\"
echo "     -d '{\"instanceName\":\"scout-wa\",\"qrcode\":true,\"integration\":\"WHATSAPP-BAILEYS\"}'"
echo ""
echo "2. Pegar QR Code (salva em /opt/scout/qrcode.png):"
echo "   bash $INSTALL_DIR/deploy/get-qrcode.sh"
echo ""
echo "3. Configurar webhook na Evolution:"
echo "   bash $INSTALL_DIR/deploy/setup-webhook.sh"
echo ""
echo "Logs: docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
echo "Cron logs: tail -f $INSTALL_DIR/logs/cron-*.log"
