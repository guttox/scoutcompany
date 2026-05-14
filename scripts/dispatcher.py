"""
Processa a fila ~/scout/data/fila_envio.json e dispara mensagens WhatsApp
via Evolution API quando bate o horário programado.

Respeita:
  - Janelas globais (8h-20h, seg-sex; sáb até 14h; dom nunca)
  - MAX_DISPAROS_DIA (default 25) — incluindo dry-run
  - Intervalo aleatório 3-5 min entre envios reais (em modo dry-run ignora)
  - SCOUT_DRY_RUN=1 → só simula e loga em logs/disparos.log
  - Mensagem "agendado_para" no passado → elegível para envio

Atualiza pipeline.csv com data/hora de envio e status "Abordado".

Uso:
  python3 dispatcher.py             # processa fila uma vez
  python3 dispatcher.py --max 5     # processa no máximo 5 itens
  python3 dispatcher.py --force     # ignora janela global (DEBUG)
"""
import argparse
import random
import sys
import time
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    DISPAROS_LOG, INTERVALO_MAX_SEG, INTERVALO_MIN_SEG,
    PIPELINE_CSV, PIPELINE_FIELDS,
    calcular_max_disparos_hoje, calcular_semana_atual,
    dispatch_dry_run, env, is_horario_habil, load_env, log, log_disparo,
    marcar_primeiro_disparo_se_preciso, numeros_com_conversa, read_csv,
    read_fila, registrar_volume_dia, send_whatsapp_via_evolution, write_csv,
    write_fila,
)


def _digits(s):
    return "".join(c for c in str(s or "") if c.isdigit())


def _enviado_hoje(numero):
    """True se DISPAROS_LOG tem OK/DRY_RUN/FAIL hoje contendo o número."""
    d = _digits(numero)
    if not d or not DISPAROS_LOG.exists():
        return False
    hoje_str = date.today().isoformat()
    with open(DISPAROS_LOG, encoding="utf-8") as f:
        for line in f:
            if not line.startswith(f"[{hoje_str}"):
                continue
            if " RODADA " in line:
                continue
            if d in line and (("OK " in line) or ("DRY_RUN " in line) or ("FAIL " in line)):
                return True
    return False


def _tentativas_hoje():
    """Conta TENTATIVAS de disparo hoje (OK + DRY_RUN + FAIL).
    Limite diário é por tentativa, não por sucesso — falha conta no limite."""
    if not DISPAROS_LOG.exists():
        return 0
    hoje_str = date.today().isoformat()
    n = 0
    with open(DISPAROS_LOG, encoding="utf-8") as f:
        for line in f:
            if not line.startswith(f"[{hoje_str}"):
                continue
            # tipos de linha de disparo: "OK ", "FAIL ", "DRY_RUN " (linha RODADA é resumo, ignora)
            if " RODADA " in line:
                continue
            if ("OK " in line) or ("FAIL " in line) or ("DRY_RUN " in line):
                n += 1
    return n


def atualizar_pipeline_envio(prospect_id):
    """Marca prospect_id como Abordado no pipeline.csv com data_envio_site preenchido."""
    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        return False
    found = False
    for row in pipeline:
        if row.get("id") == prospect_id:
            if not row.get("status") or row["status"] in ("", "Sem contato"):
                row["status"] = "Abordado"
            if not row.get("data_abordagem"):
                row["data_abordagem"] = date.today().isoformat()
            if not row.get("data_envio_site"):
                row["data_envio_site"] = datetime.now().isoformat(timespec="seconds")
            found = True
            break
    if found:
        write_csv(PIPELINE_CSV, pipeline, PIPELINE_FIELDS)
    return found


def atualizar_pipeline_falha(prospect_id, motivo):
    """Marca prospect_id como Falha no pipeline.csv."""
    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        return False
    found = False
    for row in pipeline:
        if row.get("id") == prospect_id:
            row["status"] = "Falha"
            obs_atual = row.get("observacao", "") or ""
            row["observacao"] = (obs_atual + f" | falha envio: {motivo}").strip(" |")[:300]
            if not row.get("data_envio_site"):
                row["data_envio_site"] = datetime.now().isoformat(timespec="seconds")
            found = True
            break
    if found:
        write_csv(PIPELINE_CSV, pipeline, PIPELINE_FIELDS)
    return found


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=None, help="Máximo de envios nessa rodada")
    parser.add_argument("--force", action="store_true",
                        help="Ignora janela global de horário (debug)")
    args = parser.parse_args()

    # Inicializa cedo pra evitar NameError em qualquer return precoce.
    # tentativas_hoje (int) ≠ tentados_hoje (set): contador do dia vs dedup da rodada.
    tentativas_hoje = 0
    tentativas_rodada = 0
    tentados_hoje = set()

    dry_run = dispatch_dry_run()
    semana = calcular_semana_atual()
    max_dia = calcular_max_disparos_hoje()
    log(f"Aquecimento: semana {semana} · limite hoje {max_dia} disparos")

    agora = datetime.now()
    if not args.force and not is_horario_habil(agora):
        log(f"Fora de horário hábil ({agora:%H:%M %a}) — dispatcher dormindo", "INFO")
        return

    tentativas_hoje = _tentativas_hoje()
    if tentativas_hoje >= max_dia:
        log(f"Limite diário atingido: {tentativas_hoje}/{max_dia}", "WARN")
        return

    fila = read_fila()
    # Dedup cross-day forte: nunca repete número/id já abordado em qualquer momento.
    # Comparações sempre por dígitos puros — formatos divergem (wa.me, +55, etc).
    contactados_alguma_vez_num = set()
    contactados_alguma_vez_id = set()
    for x in fila["items"]:
        if x.get("status") in ("enviado", "dryrun", "falha"):
            d = _digits(x.get("whatsapp"))
            if d:
                contactados_alguma_vez_num.add(d)
            if x.get("id"):
                contactados_alguma_vez_id.add(x["id"])
    # Pipeline.csv: prospects abordados manualmente ou em rodadas anteriores.
    for p in read_csv(PIPELINE_CSV):
        status = (p.get("status") or "").strip().lower()
        ja_marcado = (status and status not in ("", "novo", "sem contato")) or bool(p.get("data_envio_site"))
        if not ja_marcado:
            continue
        if p.get("id"):
            contactados_alguma_vez_id.add(p["id"])
        d = _digits(p.get("contato"))
        if d:
            contactados_alguma_vez_num.add(d)
    # ★ Sinal mais forte: número tem arquivo em conversas/ → JÁ engajado (envio
    # inicial + qualquer resposta cria o arquivo). Nunca reenvia prospecção.
    contactados_alguma_vez_num |= numeros_com_conversa()

    pendentes = [x for x in fila["items"] if x.get("status") == "pendente"]
    elegiveis = []
    pulados_dedup = 0
    for it in pendentes:
        try:
            alvo = datetime.fromisoformat(it["agendado_para"])
        except Exception:
            continue
        if alvo > agora:
            continue
        d_wa = _digits(it.get("whatsapp"))
        if (d_wa and d_wa in contactados_alguma_vez_num) \
                or it.get("id") in contactados_alguma_vez_id:
            it["status"] = "skip_duplicado"
            it["skipado_em"] = agora.isoformat(timespec="seconds")
            pulados_dedup += 1
            log(f"[SKIP] número {d_wa} já tem conversa/abordado — pulando prospecção", "INFO")
            continue
        # Defesa adicional "hoje": disparos.log já tem entrada hoje pra esse número.
        if d_wa and _enviado_hoje(d_wa):
            it["status"] = "skip_duplicado_hoje"
            it["skipado_em"] = agora.isoformat(timespec="seconds")
            pulados_dedup += 1
            log(f"[SKIP] número {d_wa} já recebeu mensagem hoje", "INFO")
            continue
        elegiveis.append(it)
    if pulados_dedup:
        log(f"Dedup pulou {pulados_dedup} item(s) da fila (já contactados antes)")

    if not elegiveis:
        log("Nenhum item elegível agora", "INFO")
        return

    log(f"Dispatcher: {len(elegiveis)} elegíveis · tentativas hoje {tentativas_hoje}/{max_dia} · "
        f"DRY_RUN={dry_run}")

    limite_rodada = args.max if args.max else max_dia - tentativas_hoje

    # Ordena pelos mais antigos primeiro (FIFO)
    elegiveis.sort(key=lambda x: x.get("agendado_para", ""))

    for item in elegiveis:
        if tentativas_rodada >= limite_rodada:
            break
        if tentativas_hoje + tentativas_rodada >= max_dia:
            log(f"Bateu limite diário durante rodada: {max_dia}", "WARN")
            break

        # Guarda de horário durante a rodada: se cruzou a borda (ex: passou das
        # 19h enquanto dormíamos no intervalo), para a rodada — itens sobrantes
        # ficam pra próxima.
        if not args.force and not is_horario_habil():
            log("Cruzou borda de horário hábil — encerrando rodada", "INFO")
            break

        numero = item.get("whatsapp", "")
        texto = item.get("mensagem", "")
        nome = item.get("nome", item.get("id"))
        if not numero or not texto:
            item["status"] = "invalido"
            continue
        numero_digits = _digits(numero)
        if numero_digits in tentados_hoje:
            # dois itens na fila com mesmo número — dispara só o primeiro
            item["status"] = "skip_duplicado"
            item["skipado_em"] = datetime.now().isoformat(timespec="seconds")
            log(f"[SKIP] número {numero_digits} já tentado nessa rodada", "INFO")
            continue

        # Registra primeiro disparo (se ainda não há) — marca semana 1 dia 1
        marcar_primeiro_disparo_se_preciso()

        # já contabiliza tentativa antes do envio (limite é por tentativa)
        tentativas_rodada += 1
        resp = send_whatsapp_via_evolution(numero, texto, dry_run=dry_run)
        agora_iso = datetime.now().isoformat(timespec="seconds")

        if resp["ok"]:
            item["status"] = "dryrun" if resp["dry_run"] else "enviado"
            item["enviado_em"] = agora_iso
            atualizar_pipeline_envio(item.get("id"))
            log(f"→ {'[DRY] ' if resp['dry_run'] else ''}{nome} ({numero})")
        else:
            item["status"] = "falha"
            item["ultimo_erro"] = resp.get("status")
            item["falhou_em"] = agora_iso
            atualizar_pipeline_falha(item.get("id"), resp.get("status", "?"))
            log(f"✗ FALHA {nome} ({numero}): {resp.get('status')} — segue pro próximo", "ERROR")

        tentados_hoje.add(numero_digits)

        # Intervalo aleatório só em LIVE (vale pra sucesso E falha)
        if not dry_run and tentativas_rodada < limite_rodada:
            wait = random.randint(INTERVALO_MIN_SEG, INTERVALO_MAX_SEG)
            log(f"Aguardando {wait}s antes da próxima tentativa", "INFO")
            time.sleep(wait)

    write_fila(fila)
    log(f"Dispatcher fim: {tentativas_rodada} tentativa(s) nessa rodada")
    log_disparo(f"RODADA fim: {tentativas_rodada} tentativa(s) · DRY_RUN={dry_run} "
                f"· total_dia={tentativas_hoje + tentativas_rodada}/{max_dia}")
    # Registra/atualiza linha do dia em volume.log
    registrar_volume_dia(extras={"dry_run": int(dry_run)})


if __name__ == "__main__":
    main()
