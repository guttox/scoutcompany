#!/usr/bin/env bash
# Pipeline do domingo às 20h: aprende com a semana → gera + envia relatório.
set -u
ROOT="$HOME/scout"
VENV="$ROOT/venv"
LOG="$ROOT/logs/weekly.log"
mkdir -p "$ROOT/logs"

echo "═══════════════════════════════" >> "$LOG"
echo "WEEKLY PIPELINE — $(date -u +%FT%TZ)" >> "$LOG"
echo "═══════════════════════════════" >> "$LOG"

"$VENV/bin/python" "$ROOT/scripts/analyze_week.py" >> "$LOG" 2>&1
"$VENV/bin/python" "$ROOT/scripts/weekly_report.py" >> "$LOG" 2>&1

echo "Weekly pipeline finished: $(date -u +%FT%TZ)" >> "$LOG"
