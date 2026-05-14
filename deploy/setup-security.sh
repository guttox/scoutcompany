#!/usr/bin/env bash
# Scout — hardening de segurança da VPS (Ubuntu 22.04+).
#
# O QUE FAZ:
#   1. UFW: nega tudo, libera só 22/tcp (SSH).
#   2. fail2ban: bane IPs após 5 tentativas SSH em 10 min (1h de ban) +
#      notifica no Telegram via /opt/scout/.env (TELEGRAM_TOKEN/CHAT_ID).
#   3. unattended-upgrades: patches de segurança automáticos.
#   4. SSH: PermitEmptyPasswords no, MaxAuthTries 3 (mínimo invasivo —
#      NÃO mexe em PasswordAuthentication nem PermitRootLogin pra não
#      trancar o Augusto fora).
#   5. Cron de health check (hora cheia): /opt/scout/scripts/health_check.py.
#
# PRÉ-REQUISITO:
#   - 8080 e 5005 devem estar bindados em 127.0.0.1 no docker-compose.yml.
#     UFW não bloqueia portas mapeadas pelo Docker (Docker manipula iptables
#     direto e fica antes das regras do UFW). O patch já foi feito no
#     docker-compose.yml — o script só verifica e aborta se voltar pra 0.0.0.0.
#
# COMO USAR (na VPS, como root):
#   bash /opt/scout/deploy/setup-security.sh
#
# Idempotente: pode rodar várias vezes.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/scout}"
ENV_FILE="$INSTALL_DIR/.env"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"

echo "════════════════════════════════════════"
echo "  SCOUT — HARDENING DE SEGURANÇA"
echo "════════════════════════════════════════"

if [ "$EUID" -ne 0 ]; then
    echo "✗ Rode como root (sudo bash $0)" >&2
    exit 1
fi

if ! command -v apt-get >/dev/null; then
    echo "✗ Script assume Ubuntu/Debian (apt-get)." >&2
    exit 1
fi

# ───────────── 0. Sanity check: docker-compose binda em 127.0.0.1 ─────────────
echo ""
echo "→ [0/5] Conferindo bindings do docker-compose"
if [ -f "$COMPOSE_FILE" ]; then
    if grep -E '^\s*-\s*"(5005|8080):(5005|8080)"' "$COMPOSE_FILE" >/dev/null; then
        echo "  ✗ docker-compose.yml ainda expõe 5005/8080 em 0.0.0.0." >&2
        echo "  ✗ UFW NÃO bloqueia portas do Docker — corrija pra 127.0.0.1:5005:5005 antes." >&2
        exit 1
    fi
    echo "  ✓ Portas Scout/Evolution restritas a 127.0.0.1"
else
    echo "  ⚠ $COMPOSE_FILE não encontrado — pulando sanity check"
fi

# ───────────── 1. UFW ─────────────
echo ""
echo "→ [1/5] UFW firewall"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ufw

# Reset suave: garante estado consistente sem perder a sessão SSH atual.
# (ufw allow SSH antes de enable evita lockout — UFW também detecta sessão.)
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
# Postgres 5432 e Redis 6379 não estão expostos pelo compose, mas reforçamos
# a negação no firewall por segurança em profundidade.
ufw deny 5432/tcp comment "Postgres (só interno)"
ufw deny 6379/tcp comment "Redis (só interno)"
ufw --force enable
echo "  ✓ UFW ativo:"
ufw status verbose | sed 's/^/    /'

# ───────────── 2. fail2ban com notificação Telegram ─────────────
echo ""
echo "→ [2/5] fail2ban + alerta Telegram"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban curl

if [ ! -f "$ENV_FILE" ]; then
    echo "  ✗ $ENV_FILE não encontrado — não consigo ler TELEGRAM_TOKEN/CHAT_ID" >&2
    exit 1
fi

# shellcheck disable=SC1090
TG_TOKEN=$(grep -E '^TELEGRAM_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
TG_CHAT=$(grep -E '^TELEGRAM_CHAT_ID=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")

if [ -z "$TG_TOKEN" ] || [ -z "$TG_CHAT" ]; then
    echo "  ✗ TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID vazios em $ENV_FILE" >&2
    exit 1
fi

# Arquivo de credenciais privado (modo 600, só root)
install -d -m 700 /etc/scout-security
umask 077
cat > /etc/scout-security/telegram.env <<EOF
TELEGRAM_TOKEN=$TG_TOKEN
TELEGRAM_CHAT_ID=$TG_CHAT
EOF
chmod 600 /etc/scout-security/telegram.env
echo "  ✓ Credenciais Telegram em /etc/scout-security/telegram.env (mode 600)"

# Action: chama curl pra Telegram quando um IP é banido.
# fail2ban substitui <ip>, <failures>, <name> automaticamente.
cat > /etc/fail2ban/action.d/telegram-scout.conf <<'EOF'
# Scout — notificação Telegram on ban.
# Lê TELEGRAM_TOKEN/TELEGRAM_CHAT_ID de /etc/scout-security/telegram.env.

[Definition]
actionstart =
actionstop =
actioncheck =
actionban = bash -c 'source /etc/scout-security/telegram.env; \
            curl -sS --max-time 10 \
              -d "chat_id=$TELEGRAM_CHAT_ID" \
              --data-urlencode "text=🔒 Scout VPS: IP <ip> banido por fail2ban (jail <name>, <failures> tentativas)" \
              "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage" >/dev/null'
actionunban =
EOF

# jail.local: 5 tentativas em 10 min, ban de 1h, com Telegram + ação padrão.
cat > /etc/fail2ban/jail.local <<'EOF'
# Scout — política sshd customizada.
[DEFAULT]
backend = systemd
banaction = ufw

[sshd]
enabled = true
maxretry = 5
findtime = 600
bantime = 3600
action = %(action_)s
         telegram-scout
EOF

systemctl enable fail2ban >/dev/null
systemctl restart fail2ban
sleep 1
echo "  ✓ fail2ban ativo:"
fail2ban-client status sshd 2>/dev/null | sed 's/^/    /' || true

# ───────────── 3. unattended-upgrades ─────────────
echo ""
echo "→ [3/5] unattended-upgrades"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades
# Garante que ESM e security estão habilitados (padrão Ubuntu 22.04+)
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
EOF
echo "  ✓ unattended-upgrades configurado"

# ───────────── 4. SSH hardening (mínimo invasivo) ─────────────
echo ""
echo "→ [4/5] SSH hardening (não mexe em PasswordAuth nem PermitRoot)"
SSHD_CONF="/etc/ssh/sshd_config.d/99-scout-hardening.conf"
cat > "$SSHD_CONF" <<'EOF'
# Scout — endurece sshd. NÃO desabilita PasswordAuthentication nem PermitRootLogin
# pra não trancar o operador fora. Ajustes só nas tentativas e em senha vazia.
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
EOF
if sshd -t 2>/dev/null; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    echo "  ✓ $SSHD_CONF aplicado e sshd recarregado"
else
    echo "  ✗ sshd -t falhou — config $SSHD_CONF removida pra não quebrar SSH" >&2
    rm -f "$SSHD_CONF"
    exit 1
fi

# ───────────── 5. Cron do health check ─────────────
echo ""
echo "→ [5/5] Cron do health_check.py (hora cheia)"
HEALTH_PY="$INSTALL_DIR/scripts/health_check.py"
if [ ! -f "$HEALTH_PY" ]; then
    echo "  ⚠ $HEALTH_PY não existe ainda — instale o script e re-rode pra criar o cron"
else
    cat > /etc/cron.d/scout-health <<EOF
# Scout — health check horário (alerta no Telegram se algo cair).
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 * * * * root /usr/bin/python3 $HEALTH_PY >> $INSTALL_DIR/logs/health_check.log 2>&1
EOF
    chmod 644 /etc/cron.d/scout-health
    echo "  ✓ /etc/cron.d/scout-health criado"
fi

echo ""
echo "════════════════════════════════════════"
echo "  HARDENING APLICADO"
echo "════════════════════════════════════════"
echo "Resumo:"
echo "  • UFW: 22/tcp liberado, demais portas negadas"
echo "  • fail2ban: 5 falhas SSH em 10min → ban 1h + Telegram"
echo "  • unattended-upgrades: ligado"
echo "  • SSH: MaxAuthTries=3, PermitEmptyPasswords=no"
echo "  • Health check: roda toda hora cheia"
echo ""
echo "Para validar fail2ban manualmente:"
echo "  fail2ban-client status sshd"
echo "Para conferir UFW:"
echo "  ufw status verbose"
