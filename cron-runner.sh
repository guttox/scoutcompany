#!/bin/bash
# Wrapper executado pelo cron — ambiente isolado, paths absolutos.
# Editado por: cron-on.sh / cron-off.sh
set -e
export HOME=/Users/augustobarbosa
cd "$HOME/scout"

# Garante que pip user packages (anthropic, googlemaps) sejam achados
export PYTHONPATH="$HOME/Library/Python/3.9/lib/python/site-packages:${PYTHONPATH:-}"

mkdir -p logs
TS=$(date '+%Y-%m-%d %H:%M:%S')
echo "" >> logs/cron.log
echo "═══════ $TS — CRON RUN ═══════" >> logs/cron.log

# Variedade forçada por categoria — soma 30 disparos/dia, distribuídos:
#   6 restaurantes/delivery · 5 salões/barbearias · 5 clínicas/dentistas
#   4 petshops · 4 lojas/comércio · 3 escritórios/B2B · 3 outros
# Sem flags = modo cotas (default). Cotas custom em data/cotas_segmentos.json.
/usr/bin/python3 -W ignore scripts/run_all.py --top 15 >> logs/cron.log 2>&1
