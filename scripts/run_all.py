"""
Orquestrador end-to-end. Roda Etapas 1 → 6 em sequência.

Pipeline:
  0   morning_heartbeat   — avisa no Telegram que tá ativo
  1   search_prospects    — busca em Google Places (loop nas cidades até META)
  2   qualify             — score 1-10
  2.5 enrich_contacts     — valida WhatsApp + busca email
  3   generate_messages   — WhatsApp + Email personalizados
  5   pipeline_report sync — adiciona qualificados à pipeline
  4   send_telegram       — digest (desativado por default)
  6   enqueue_dispatch    — fila com agendamento por segmento

Diferencial: o run_all roda em LOOP por batches de cidades do rodízio
até atingir META_WHATSAPPS_DIA (25 na semana 1) prospects qualificados
com WhatsApp válido OU esgotar as 15 cidades do rodízio.

Uso:
  python3 run_all.py                  # roda completo (loop até bater meta)
  python3 run_all.py --meta 25        # define meta de WhatsApps válidos
  python3 run_all.py --max 10         # limita TOTAL de prospects buscados
  python3 run_all.py --no-telegram    # não envia ao Telegram
  python3 run_all.py --skip-enrich    # pula enrichment (debug)
"""
import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    PIPELINE_CSV, QUALIFICADOS_CSV, COTAS_DIA_TMP_PATH,
    calcular_max_disparos_hoje, env, is_truthy, load_env, log,
    read_config, read_csv, read_fila, write_config,
    montar_cotas_dia, salvar_cotas_dia, _segmento_para_categoria,
)

import search_prospects
import qualify
import enrich_contacts
import generate_messages
import send_telegram
import pipeline_report
import enqueue_dispatch
import morning_heartbeat


CIDADES_POR_BATCH = 3


def _digits(s):
    return "".join(c for c in str(s or "") if c.isdigit())


def _numeros_indisponiveis():
    """Números que NÃO podem ser contados pra meta de hoje:
      - pipeline.csv com status preenchido (já abordados antes)
      - fila com status enviado/dryrun/falha (já tentados)
      - fila com status pendente (já no estoque, aguardando janela)
    """
    nums = set()
    for p in read_csv(PIPELINE_CSV):
        status = (p.get("status") or "").strip().lower()
        if (status and status not in ("", "novo", "sem contato")) or p.get("data_envio_site"):
            n = _digits(p.get("contato"))
            if n:
                nums.add(n)
    fila = read_fila()
    for it in fila.get("items", []):
        # qualquer status (pendente, enviado, dryrun, falha) → não conta de novo
        n = _digits(it.get("whatsapp"))
        if n:
            nums.add(n)
    return nums


def _whatsapps_disponiveis():
    """Quantos prospects nos qualificados.csv têm WhatsApp válido E ainda não
    foram tocados (nem na pipeline, nem na fila)."""
    indisp = _numeros_indisponiveis()
    n = 0
    for q in read_csv(QUALIFICADOS_CSV):
        if (q.get("tem_whatsapp") or "").strip().lower() != "sim":
            continue
        wpp = q.get("whatsapp_link", "")
        numero = ""
        if "wa.me/" in wpp:
            numero = wpp.split("wa.me/", 1)[1].split("?", 1)[0]
        numero = _digits(numero)
        if not numero or numero in indisp:
            continue
        n += 1
    return n


def _pegar_proximas_cidades(start_idx, n, pool):
    """Retorna próximas n cidades circulares a partir de start_idx, sem mexer no estado."""
    out = []
    idx = start_idx % len(pool)
    for _ in range(min(n, len(pool))):
        out.append(pool[idx])
        idx = (idx + 1) % len(pool)
    return out, idx


def _rodada_busca(cidades_batch, args, cotas_json_path=None):
    """Executa search → qualify → enrich pra um batch de cidades. Idempotente
    sobre os CSVs (append + dedup)."""
    log(f"  → batch nas cidades: {cidades_batch}")

    sa = ["search_prospects.py", "--cidades", ",".join(cidades_batch)]
    if cotas_json_path:
        sa += ["--cotas-json", str(cotas_json_path)]
    else:
        if args.max:
            sa += ["--max", str(args.max)]
        if args.per_segment:
            sa += ["--per-segment", str(args.per_segment)]
        if args.segmentos:
            sa += ["--segmentos", args.segmentos]
    sys.argv = sa
    search_prospects.main()

    sys.argv = ["qualify.py"]
    qualify.main()

    if not args.skip_enrich:
        sys.argv = ["enrich_contacts.py"]
        enrich_contacts.main()


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=None, help="Máximo TOTAL de prospects buscados por batch")
    parser.add_argument("--meta", type=int, default=None,
                        help="Quantidade alvo de prospects com WhatsApp válido (default: igual ao limite diário do dispatcher)")
    parser.add_argument("--top", type=int, default=15, help="Top N pro relatório Telegram (legado)")
    parser.add_argument("--per-segment", type=int, default=None)
    parser.add_argument("--segmentos", type=str, default=None)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--telegram", action="store_true",
                        help="Força envio do digest (sobrescreve TELEGRAM_DIGEST=0)")
    parser.add_argument("--skip-enrich", action="store_true")
    args = parser.parse_args()

    log("═══════════════════════════")
    log("SCOUT PIPELINE — INÍCIO")
    log("═══════════════════════════")

    # ETAPA 0 — heartbeat matinal
    log(">>> ETAPA 0 — morning_heartbeat")
    try:
        morning_heartbeat.main()
    except Exception as e:
        log(f"heartbeat falhou (não bloqueia pipeline): {e}", "WARN")

    # ────────────────────────────────────────────────────────
    # Modo COTAS-CATEGORIA — variedade forçada por categoria.
    # Default quando nenhuma flag legada (--per-segment/--segmentos) foi
    # passada. Soma das cotas = ~30 prospects/dia distribuídos em buckets.
    # ────────────────────────────────────────────────────────
    usar_cotas = not (args.per_segment or args.segmentos)
    cotas_path = None
    cotas_hoje = None
    if usar_cotas:
        cotas_hoje = montar_cotas_dia(aplicar_aprendizado=True)
        salvar_cotas_dia(cotas_hoje)
        cotas_path = COTAS_DIA_TMP_PATH
        resumo_cotas = ", ".join(f"{c['categoria']}={c['cota']}" for c in cotas_hoje)
        meta_cotas = sum(int(c.get("cota", 0)) for c in cotas_hoje)
        log(f"▶ Cotas por categoria (total={meta_cotas}): {resumo_cotas}")

    # ────────────────────────────────────────────────────────
    # Meta de WhatsApps válidos por dia (default = limite do escalonamento)
    # Quando em modo cotas, usa o total das cotas (não o limite escalonado),
    # garantindo que TODAS as categorias sejam preenchidas.
    # ────────────────────────────────────────────────────────
    if args.meta:
        meta_wa = args.meta
    elif cotas_hoje:
        meta_wa = sum(int(c.get("cota", 0)) for c in cotas_hoje)
    else:
        meta_wa = calcular_max_disparos_hoje()

    # Lê config rodízio
    from _common import _ler_lista_cidades
    pool_cidades = _ler_lista_cidades()
    cfg = read_config()
    start_idx = int(cfg.get("rodizio", {}).get("ultimo_indice_fim", 0)) % len(pool_cidades)

    log(f"▶ Meta diária: {meta_wa} prospects com WhatsApp válido")
    log(f"▶ Pool de cidades: {len(pool_cidades)} · próximas a partir de #{start_idx}")

    # ────────────────────────────────────────────────────────
    # LOOP — busca em batches de N cidades até bater meta OU esgotar pool
    # ────────────────────────────────────────────────────────
    cidades_usadas = []
    idx_corrente = start_idx
    wa_iniciais = _whatsapps_disponiveis()
    log(f"▶ WhatsApps já disponíveis (de execuções anteriores): {wa_iniciais}")

    rodada = 0
    while True:
        wa_atual = _whatsapps_disponiveis()
        if wa_atual >= meta_wa:
            log(f"✓ Meta atingida: {wa_atual}/{meta_wa} WhatsApps disponíveis")
            break
        if len(cidades_usadas) >= len(pool_cidades):
            log(f"⚠ Esgotou pool de cidades ({len(pool_cidades)}) — parando "
                f"com {wa_atual}/{meta_wa}", "WARN")
            break

        # próximas cidades
        n_falta = max(0, len(pool_cidades) - len(cidades_usadas))
        batch_size = min(CIDADES_POR_BATCH, n_falta)
        batch, idx_corrente = _pegar_proximas_cidades(idx_corrente, batch_size, pool_cidades)

        rodada += 1
        log(f">>> ETAPA 1 (rodada {rodada}) — search/qualify/enrich | meta {wa_atual}/{meta_wa}")
        _rodada_busca(batch, args, cotas_json_path=cotas_path)
        cidades_usadas.extend(batch)

    wa_final = _whatsapps_disponiveis()
    log(f"📊 BUSCA CONCLUÍDA — {wa_final}/{meta_wa} WhatsApps válidos · "
        f"{len(cidades_usadas)} cidades cobertas: {cidades_usadas}")

    # Avança o cursor do rodízio pra próxima execução começar de onde paramos
    cfg = read_config()
    cfg.setdefault("rodizio", {})
    cfg["rodizio"]["ultimo_indice_fim"] = idx_corrente
    cfg["rodizio"]["ultimas_cidades"] = cidades_usadas[-3:] if cidades_usadas else []
    cfg["rodizio"]["total_cidades_no_pool"] = len(pool_cidades)
    write_config(cfg)

    # ETAPA 3
    log(">>> ETAPA 3 — generate_messages")
    sys.argv = ["generate_messages.py"]
    generate_messages.main()

    # ETAPA 5 sync
    log(">>> ETAPA 5 — pipeline_report sync")
    sys.argv = ["pipeline_report.py", "--sync"]
    pipeline_report.main()

    # ETAPA 4 — digest (desativado por default)
    digest_on = is_truthy(env("TELEGRAM_DIGEST", "0")) or getattr(args, "telegram", False)
    if not args.no_telegram and digest_on:
        log(">>> ETAPA 4 — send_telegram (digest)")
        sys.argv = ["send_telegram.py", "--top", str(args.top)]
        send_telegram.main()
    else:
        motivo = "--no-telegram" if args.no_telegram else "TELEGRAM_DIGEST=0"
        log(f">>> ETAPA 4 pulada ({motivo})")

    # ETAPA 6 — fila
    log(">>> ETAPA 6 — enqueue_dispatch")
    sys.argv = ["enqueue_dispatch.py"]
    enqueue_dispatch.main()

    # ────────────────────────────────────────────────────────
    # RESUMO POR CATEGORIA — quantos qualificados (com WA) caíram em cada
    # bucket vs cota. Acontece DEPOIS do enqueue pra refletir só o que vai
    # de fato pra fila do dia.
    # ────────────────────────────────────────────────────────
    if cotas_hoje:
        from collections import Counter
        indisp = _numeros_indisponiveis()  # já tocados antes
        # Conta qualificados RECENTES (não-tocados) por categoria
        contagem = Counter()
        for q in read_csv(QUALIFICADOS_CSV):
            if (q.get("tem_whatsapp") or "").strip().lower() != "sim":
                continue
            wpp = q.get("whatsapp_link", "")
            numero = ""
            if "wa.me/" in wpp:
                numero = wpp.split("wa.me/", 1)[1].split("?", 1)[0]
            numero = _digits(numero)
            if not numero:
                continue
            cat = _segmento_para_categoria(q.get("segmento", "")) or "outros"
            contagem[cat] += 1

        log("📊 Disparos hoje por categoria (real vs cota):")
        partes = []
        for bucket in cotas_hoje:
            cat = bucket["categoria"]
            cota = int(bucket.get("cota", 0))
            real = contagem.get(cat, 0)
            simbolo = "✓" if real >= cota else "⚠"
            log(f"   {simbolo} {cat}: {real}/{cota}")
            partes.append(f"{real} {cat.replace('_', '/')}")
        log("Disparos hoje: " + " · ".join(partes))

    log("═══════════════════════════")
    log(f"SCOUT PIPELINE — COMPLETO · {wa_final}/{meta_wa} WhatsApps válidos")
    log("═══════════════════════════")


if __name__ == "__main__":
    main()
