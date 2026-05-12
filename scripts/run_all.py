"""
Orquestrador end-to-end. Roda Etapas 1 → 5 em sequência.

Pipeline:
  1. search_prospects    — busca em Google Places (ou mock)
  2. qualify             — score 1-10
  2.5 enrich_contacts    — valida WhatsApp + busca email
  3. generate_messages   — WhatsApp + Email personalizados
  4. send_telegram       — digest no @usescout_bot
  5. pipeline_report sync — adiciona qualificados à pipeline

Uso:
  python3 run_all.py                  # roda completo
  python3 run_all.py --max 10         # limita prospects
  python3 run_all.py --no-telegram    # não envia ao Telegram
  python3 run_all.py --top 10         # top N pro relatório Telegram
  python3 run_all.py --skip-enrich    # pula enrichment (debug)
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import env, load_env, log

import search_prospects
import qualify
import enrich_contacts
import generate_messages
import send_telegram
import pipeline_report
import enqueue_dispatch
import morning_heartbeat


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=None, help="Máximo de prospects buscados")
    parser.add_argument("--top", type=int, default=15, help="Top N pro relatório Telegram")
    parser.add_argument("--per-segment", type=int, default=None,
                        help="Máximo de prospects por segmento (variedade)")
    parser.add_argument("--segmentos", type=str, default=None,
                        help="Lista de segmentos separados por vírgula (override do default)")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--telegram", action="store_true",
                        help="Força envio do digest (sobrescreve TELEGRAM_DIGEST=0)")
    parser.add_argument("--skip-enrich", action="store_true",
                        help="Pula a etapa de validação de canais (debug)")
    args = parser.parse_args()

    log("═══════════════════════════")
    log("SCOUT PIPELINE — INÍCIO")
    log("═══════════════════════════")

    # ETAPA 0 — heartbeat matinal pro Telegram (avisa que tá ativo)
    log(">>> ETAPA 0 — morning_heartbeat")
    try:
        morning_heartbeat.main()
    except Exception as e:
        log(f"heartbeat falhou (não bloqueia pipeline): {e}", "WARN")

    # ETAPA 1
    log(">>> ETAPA 1 — search_prospects")
    sa = ["search_prospects.py"]
    if args.max:
        os.environ["MAX_PROSPECTS"] = str(args.max)
        sa += ["--max", str(args.max)]
    if args.per_segment:
        sa += ["--per-segment", str(args.per_segment)]
    if args.segmentos:
        sa += ["--segmentos", args.segmentos]
    sys.argv = sa
    search_prospects.main()

    # ETAPA 2
    log(">>> ETAPA 2 — qualify")
    sys.argv = ["qualify.py"]
    qualify.main()

    # ETAPA 2.5 — enrichment
    if not args.skip_enrich:
        log(">>> ETAPA 2.5 — enrich_contacts (WhatsApp + email)")
        sys.argv = ["enrich_contacts.py"]
        enrich_contacts.main()
    else:
        log(">>> ETAPA 2.5 pulada (--skip-enrich)")

    # ETAPA 3
    log(">>> ETAPA 3 — generate_messages")
    sys.argv = ["generate_messages.py"]
    generate_messages.main()

    # ETAPA 5 sync (adiciona pipeline) — antes do envio
    log(">>> ETAPA 5 — pipeline_report sync")
    sys.argv = ["pipeline_report.py", "--sync"]
    pipeline_report.main()

    # ETAPA 4 — digest de prospects no Telegram (ruidoso em modo LIVE)
    # Desativada por default agora que dispatcher manda WhatsApp sozinho.
    # Ativar de novo: TELEGRAM_DIGEST=1 no .env ou flag --telegram.
    from _common import is_truthy
    digest_on = is_truthy(env("TELEGRAM_DIGEST", "0")) or getattr(args, "telegram", False)
    if not args.no_telegram and digest_on:
        log(">>> ETAPA 4 — send_telegram (digest)")
        sys.argv = ["send_telegram.py", "--top", str(args.top)]
        send_telegram.main()
    else:
        motivo = "--no-telegram" if args.no_telegram else "TELEGRAM_DIGEST=0"
        log(f">>> ETAPA 4 pulada ({motivo}) — usar Telegram só pra heartbeat/lead quente/relatório")

    # ETAPA 6 — fila de disparo WhatsApp (Evolution) com agendamento por segmento
    log(">>> ETAPA 6 — enqueue_dispatch (fila WhatsApp por segmento)")
    sys.argv = ["enqueue_dispatch.py"]
    enqueue_dispatch.main()

    log("═══════════════════════════")
    log("SCOUT PIPELINE — COMPLETO")
    log("═══════════════════════════")


if __name__ == "__main__":
    main()
