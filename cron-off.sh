#!/bin/bash
# Desliga o cron do Scout (remove apenas as linhas com tag # scout-).
set -e

TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v '# scout-' > "$TMP" || true
# Remove também as linhas de comando que vêm DEPOIS de uma tag scout-
# (no nosso caso, a tag e o comando são linhas separadas — vou fazer mais robusto)

# Reescreve filtrando linhas relacionadas ao Scout
crontab -l 2>/dev/null | awk '
  /# scout-/ { skip=1; next }
  skip { skip=0; next }
  { print }
' > "$TMP" || true

if [ -s "$TMP" ]; then
  crontab "$TMP"
else
  crontab -r 2>/dev/null || true
fi
rm "$TMP"

echo "🔕 Cron Scout DESATIVADO"
echo ""
echo "Verificar com: crontab -l"
echo "Reativar com:  bash ~/scout/cron-on.sh"
