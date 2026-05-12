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

# Variedade: 5 segmentos × 4 cada = 20 prospects/dia
/usr/bin/python3 -W ignore scripts/run_all.py \
  --max 20 --per-segment 4 --top 15 \
  --segmentos "barbearia,salao de beleza,petshop,clinica odontologica,advocacia,academia,contabilidade,otica,imobiliaria,studio pilates" \
  >> logs/cron.log 2>&1
