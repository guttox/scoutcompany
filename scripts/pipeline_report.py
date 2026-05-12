"""
Etapa 5 — pipeline de acompanhamento.

Funcionalidades:
- Sincroniza prospects qualificados pra pipeline.csv (cria entries com status "Novo")
- Atualiza status via CLI (--update)
- Gera relatório semanal pra Telegram
- Status válidos: Novo, Abordado, Respondeu, Reunião, Fechado, Perdido

Uso:
  python3 pipeline_report.py --sync                      # adiciona qualificados como "Novo"
  python3 pipeline_report.py --update <id> --status Abordado
  python3 pipeline_report.py --weekly                    # envia resumo da semana
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    PIPELINE_CSV, PIPELINE_FIELDS, QUALIFICADOS_CSV,
    env, load_env, log, read_csv, write_csv,
)
from send_telegram import telegram_send

STATUS_VALIDOS = ["Novo", "Abordado", "Respondeu", "Reunião", "Fechado", "Perdido"]


def load_pipeline():
    return read_csv(PIPELINE_CSV)


def save_pipeline(rows):
    write_csv(PIPELINE_CSV, rows, PIPELINE_FIELDS)


def sync():
    """Adiciona qualificados que ainda não estão na pipeline."""
    qualificados = read_csv(QUALIFICADOS_CSV)
    pipeline = load_pipeline()
    existing_ids = {r.get("id") for r in pipeline}

    novos = []
    for q in qualificados:
        if q.get("id") in existing_ids:
            continue
        novos.append({
            "id": q.get("id", ""),
            "nome": q.get("nome", ""),
            "segmento": q.get("segmento", ""),
            "contato": q.get("telefone") or q.get("instagram") or "",
            "data_abordagem": "",
            "status": "Novo",
            "observacao": "",
        })

    if novos:
        pipeline.extend(novos)
        save_pipeline(pipeline)
        log(f"✅ {len(novos)} prospects adicionados à pipeline")
    else:
        log("Nenhum prospect novo pra adicionar")
    return novos


def update_status(prospect_id, novo_status, observacao=""):
    if novo_status not in STATUS_VALIDOS:
        log(f"Status inválido: {novo_status}. Use: {STATUS_VALIDOS}", "ERROR")
        return False
    pipeline = load_pipeline()
    found = False
    for row in pipeline:
        if row.get("id") == prospect_id:
            row["status"] = novo_status
            if novo_status == "Abordado" and not row.get("data_abordagem"):
                row["data_abordagem"] = datetime.now().strftime("%Y-%m-%d")
            if observacao:
                row["observacao"] = observacao
            found = True
            break
    if not found:
        log(f"ID '{prospect_id}' não encontrado na pipeline", "ERROR")
        return False
    save_pipeline(pipeline)
    log(f"✅ {prospect_id} → {novo_status}")
    return True


def weekly_report():
    pipeline = load_pipeline()
    counts = {s: 0 for s in STATUS_VALIDOS}
    for row in pipeline:
        s = row.get("status", "")
        if s in counts:
            counts[s] += 1

    total = sum(counts.values())
    today = datetime.now().strftime("%d/%m/%Y")

    msg = f"""🔍 Scout — Prospecção Inteligente
📊 PIPELINE DA SEMANA — {today}

Total na pipeline: {total}

✅ Fechados: {counts['Fechado']}
🤝 Em negociação: {counts['Reunião'] + counts['Respondeu']}
📨 Abordados: {counts['Abordado']}
🆕 Novos (a abordar): {counts['Novo']}
❌ Perdidos: {counts['Perdido']}

Detalhe:
• Reunião marcada: {counts['Reunião']}
• Respondeu: {counts['Respondeu']}

━━━━━━━━━━━━━━━
Scout by Augusto Barbosa"""

    if telegram_send(msg):
        log("✅ Relatório semanal enviado")
    else:
        log("Falha ao enviar relatório semanal", "ERROR")
    return msg


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true", help="Adiciona qualificados como Novo")
    parser.add_argument("--update", help="ID do prospect a atualizar")
    parser.add_argument("--status", help=f"Novo status: {STATUS_VALIDOS}")
    parser.add_argument("--obs", default="", help="Observação")
    parser.add_argument("--weekly", action="store_true", help="Envia relatório semanal")
    args = parser.parse_args()

    if args.sync:
        sync()
    elif args.update and args.status:
        update_status(args.update, args.status, args.obs)
    elif args.weekly:
        weekly_report()
    else:
        # default: print summary stdout
        pipeline = load_pipeline()
        print(f"Pipeline tem {len(pipeline)} prospects")
        counts = {}
        for row in pipeline:
            s = row.get("status", "?")
            counts[s] = counts.get(s, 0) + 1
        for s, n in counts.items():
            print(f"  {s}: {n}")


if __name__ == "__main__":
    main()
