#!/bin/bash
# Liga o Scout pra rodar todo dia às 9h + relatório semanal sexta 18h.
set -e

SCOUT="$HOME/scout"
RUNNER="$SCOUT/cron-runner.sh"
WEEKLY_CMD="cd $SCOUT && /usr/bin/python3 -W ignore scripts/pipeline_report.py --weekly >> $SCOUT/logs/cron.log 2>&1"
DAILY_CMD="$RUNNER"

chmod +x "$RUNNER"

# Cria crontab atual sem linhas antigas do Scout
TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v '# scout-' > "$TMP" || true

# Adiciona as 2 linhas marcadas com tags
cat >> "$TMP" <<EOF
# scout-daily — busca de leads todo dia 9h
0 9 * * * $DAILY_CMD
# scout-weekly — relatório de pipeline sexta 18h
0 18 * * 5 $WEEKLY_CMD
EOF

crontab "$TMP"
rm "$TMP"

echo "✅ Cron Scout ATIVO"
echo ""
echo "Verificar com: crontab -l | grep scout-"
echo "Desativar com: bash ~/scout/cron-off.sh"
echo ""
echo "Próxima execução:"
echo "  • Diário (busca): hoje/amanhã às 09:00"
echo "  • Semanal (pipeline): próxima sexta às 18:00"
echo ""
crontab -l | grep -A1 'scout-'
