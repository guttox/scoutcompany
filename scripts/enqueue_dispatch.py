"""
Adiciona qualificados com WhatsApp à fila de envio (data/fila_envio.json),
calculando próxima janela hábil baseada no segmento.

NÃO envia mensagem nenhuma — só agenda. O `dispatcher.py` é quem processa
a fila no horário certo.

Rodado como última etapa do run_all.py.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    MENS_DIR, PIPELINE_CSV, QUALIFICADOS_CSV,
    enqueue_dispatch, load_env, log,
    next_send_window, read_csv, read_fila,
)

# Statuses no pipeline.csv que indicam "já abordado, não tocar de novo"
STATUS_JA_ABORDADO = {
    "abordado", "reuniao", "reunião", "fechado", "lead quente",
    "perdido", "falha", "respondeu",
}


def _digits(s):
    return "".join(c for c in str(s or "") if c.isdigit())


def carregar_ja_contactados():
    """Retorna 2 conjuntos: (ids_ja_abordados, numeros_ja_abordados).

    Fontes consideradas:
      - pipeline.csv com status != vazio/'Novo'/'Sem contato'
      - pipeline.csv com data_envio_site preenchida
      - fila_envio.json com items status enviado/dryrun/falha (qualquer dia)
    """
    ids = set()
    numeros = set()

    for p in read_csv(PIPELINE_CSV):
        status = (p.get("status") or "").strip().lower()
        ja = (status and status not in ("", "novo", "sem contato")) or bool(p.get("data_envio_site"))
        if ja:
            if p.get("id"):
                ids.add(p["id"])
            num = _digits(p.get("contato"))
            if num:
                numeros.add(num)

    fila = read_fila()
    for it in fila.get("items", []):
        if it.get("status") in ("enviado", "dryrun", "falha", "invalido"):
            if it.get("id"):
                ids.add(it["id"])
            num = _digits(it.get("whatsapp"))
            if num:
                numeros.add(num)

    return ids, numeros


def extrair_mensagem_whatsapp(txt_path):
    """Pega só a seção WHATSAPP do arquivo de mensagem.

    Estrutura esperada:
        # cabeçalho...
        ═══════
        WHATSAPP
        ═══════

        <corpo>

        ═══════
        EMAIL — Assunto:
        ...
    """
    if not txt_path.exists():
        return ""
    raw = txt_path.read_text(encoding="utf-8")

    in_section = False
    lines_out = []
    for line in raw.splitlines():
        stripped = line.strip()
        # Marcador WHATSAPP isolado na linha → começa coleta na próxima
        if not in_section:
            if stripped == "WHATSAPP":
                in_section = True
                continue
            continue
        # Em seção: para se achar linha "EMAIL" ou similar
        if stripped.startswith("EMAIL"):
            break
        # Ignora linhas só com separador ═
        if stripped and all(ch == "═" for ch in stripped):
            continue
        lines_out.append(line)

    # Tira linhas vazias do começo/fim
    while lines_out and not lines_out[0].strip():
        lines_out.pop(0)
    while lines_out and not lines_out[-1].strip():
        lines_out.pop()
    return "\n".join(lines_out).strip()


def main():
    load_env()
    log(">>> enqueue_dispatch — adicionando à fila por janela de segmento")
    qualificados = read_csv(QUALIFICADOS_CSV)
    if not qualificados:
        log("Sem qualificados — fila não atualizada", "WARN")
        return

    ids_ja, numeros_ja = carregar_ja_contactados()
    log(f"Dedup base: {len(ids_ja)} ids · {len(numeros_ja)} números já contactados (nunca voltam)")

    added = 0
    skipped_sem_wpp = 0
    skipped_sem_msg = 0
    skipped_ja_contactado = 0
    for q in qualificados:
        if q.get("tem_whatsapp", "").strip().lower() != "sim":
            skipped_sem_wpp += 1
            continue
        wpp_link = q.get("whatsapp_link", "")
        # extrai dígitos depois de wa.me/
        numero = ""
        if "wa.me/" in wpp_link:
            numero = wpp_link.split("wa.me/", 1)[1].split("?", 1)[0]
        numero = _digits(numero)
        if not numero:
            skipped_sem_wpp += 1
            continue

        pid = q.get("id")
        # DEDUP FORTE: pula se id OU número já foi tocado (cross-day, cross-source)
        if pid in ids_ja or numero in numeros_ja:
            skipped_ja_contactado += 1
            continue

        msg_path = MENS_DIR / f"{pid}.txt"
        mensagem = extrair_mensagem_whatsapp(msg_path)
        if not mensagem:
            skipped_sem_msg += 1
            continue

        segmento = q.get("segmento", "default")
        alvo = next_send_window(segmento)

        item = {
            "id": pid,
            "nome": q.get("nome"),
            "segmento": segmento,
            "whatsapp": numero,
            "mensagem": mensagem,
            "agendado_para": alvo.isoformat(timespec="seconds"),
            "status": "pendente",
            "adicionado_em": datetime.now().isoformat(timespec="seconds"),
            "enviado_em": None,
            "score": q.get("score"),
        }
        if enqueue_dispatch(item):
            added += 1

    log(f"enqueue_dispatch: +{added} adicionados · "
        f"{skipped_ja_contactado} já contactados (skip) · "
        f"{skipped_sem_wpp} sem WhatsApp · {skipped_sem_msg} sem mensagem")


if __name__ == "__main__":
    main()
