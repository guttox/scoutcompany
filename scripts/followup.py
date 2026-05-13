"""
Follow-up automático Scout — 48h após primeiro disparo.

Regras:
  - Roda em prospects do pipeline.csv com status="Abordado" (ou "Enviado"),
    cuja data_abordagem é >= 48h atrás.
  - Manda no máximo 1 follow-up por prospect (data_followup vazia = elegível).
  - Pula fins de semana (segunda a sexta apenas).
  - Pula fora do horário comercial 8h-19h.
  - Pula se número está na blacklist (rejeição/opt-out) ou já marcado "Rejeitado".
  - Pula se o prospect já respondeu (qualquer msg user no conversas/[numero].json
    após data_abordagem).
  - Texto do follow-up depende do `servico` (site/sistema/automacao).

Saída: atualiza pipeline.csv com data_followup + status_followup.

Cron sugerido (já no deploy/crontab.scout):
  0 10 * * 1-5 docker compose -f /opt/scout/docker-compose.yml exec -T scout python scripts/followup.py
"""
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    PIPELINE_CSV, PIPELINE_FIELDS, env, is_numero_blacklisted,
    load_conversa, load_env, log, read_csv, send_whatsapp_via_evolution,
    write_csv,
)


# Status do pipeline que indica "primeiro disparo feito".
# Codebase atual usa "Abordado"; manteve "Enviado" por compatibilidade futura.
STATUS_ELEGIVEL = {"Abordado", "Enviado"}

# Janela mínima entre disparo e follow-up (em horas)
MIN_HORAS_APOS_DISPARO = 48

# Horário comercial (inclusivo na ponta inicial, exclusivo no fim).
# Ex: HORARIO_INICIO=8, HORARIO_FIM=19 → 8:00–18:59
HORARIO_INICIO = 8
HORARIO_FIM = 19

# Texto do follow-up por serviço. Tom do Leo (consistente com responder e generate_messages).
MENSAGENS_FOLLOWUP = {
    "site": (
        "Oi! Leo aqui da Scout de novo.\n\n"
        "Só passando pra ver se você teve chance de dar uma olhada.\n\n"
        "Qualquer dúvida é só falar!"
    ),
    "sistema": (
        "Oi! Leo aqui da Scout de novo.\n\n"
        "Passou pela sua cabeça o que eu falei sobre o sistema?\n\n"
        "Qualquer dúvida é só falar!"
    ),
    "automacao": (
        "Oi! Leo aqui da Scout de novo.\n\n"
        "Só queria saber se fez sentido o que conversei com você.\n\n"
        "Qualquer dúvida é só falar!"
    ),
}


def _parse_data_abordagem(valor):
    """Aceita 'YYYY-MM-DD' ou ISO 8601 com hora. Retorna datetime ou None."""
    if not valor:
        return None
    valor = valor.strip()
    # Date-only
    try:
        d = date.fromisoformat(valor[:10])
        return datetime(d.year, d.month, d.day, 0, 0, 0)
    except ValueError:
        pass
    # ISO 8601 completo
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


def _ja_respondeu(numero, data_abordagem_dt):
    """True se há mensagem 'user' em conversas/<numero>.json após data_abordagem.
    Em caso de erro de parse, conservador: retorna False."""
    if not numero:
        return False
    conversa = load_conversa(numero)
    msgs = conversa.get("mensagens", [])
    if not msgs:
        return False
    for m in msgs:
        if m.get("role") != "user":
            continue
        ts = m.get("ts") or ""
        try:
            t = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        if t > data_abordagem_dt:
            return True
    return False


def _dentro_horario_comercial(agora=None):
    """True se é seg-sex e hora ∈ [HORARIO_INICIO, HORARIO_FIM)."""
    agora = agora or datetime.now()
    if agora.weekday() >= 5:  # 5=sábado, 6=domingo
        return False
    return HORARIO_INICIO <= agora.hour < HORARIO_FIM


def _force():
    """Permite override em teste: SCOUT_FOLLOWUP_FORCE=1 ignora horário/dia."""
    return (env("SCOUT_FOLLOWUP_FORCE", "0") or "0").strip() == "1"


def _eligivel(row, agora):
    """Retorna (eligivel: bool, motivo_skip: str|None)."""
    status = (row.get("status") or "").strip()
    if status not in STATUS_ELEGIVEL:
        return False, f"status={status}"

    if (row.get("status_followup") or "").strip():
        return False, "followup_ja_processado"
    if (row.get("data_followup") or "").strip():
        return False, "data_followup_preenchida"

    data_abord = _parse_data_abordagem(row.get("data_abordagem"))
    if not data_abord:
        return False, "data_abordagem_invalida"

    horas = (agora - data_abord).total_seconds() / 3600
    if horas < MIN_HORAS_APOS_DISPARO:
        return False, f"horas_desde_disparo={horas:.1f}"

    contato = row.get("contato") or ""
    if not contato:
        return False, "sem_contato"
    if is_numero_blacklisted(contato):
        return False, "blacklisted"

    if _ja_respondeu(contato, data_abord):
        return False, "ja_respondeu"

    return True, None


def _mensagem_para(row):
    servico = (row.get("servico") or "site").strip().lower()
    return MENSAGENS_FOLLOWUP.get(servico, MENSAGENS_FOLLOWUP["site"])


def rodar():
    load_env()
    agora = datetime.now()

    if not _force():
        if not _dentro_horario_comercial(agora):
            log(f"Fora do horário comercial ({agora.strftime('%A %H:%M')}). "
                f"Use SCOUT_FOLLOWUP_FORCE=1 pra ignorar.", "INFO")
            return 0

    rows = read_csv(PIPELINE_CSV)
    if not rows:
        log("pipeline.csv vazio — nada a fazer", "INFO")
        return 0

    enviados = 0
    pulados = {}
    alterados = False

    for row in rows:
        eligivel, motivo = _eligivel(row, agora)
        if not eligivel:
            pulados[motivo] = pulados.get(motivo, 0) + 1
            continue

        nome = row.get("nome", "(sem nome)")
        contato = row.get("contato", "")
        texto = _mensagem_para(row)

        resp = send_whatsapp_via_evolution(contato, texto)
        agora_iso = datetime.now().isoformat(timespec="seconds")
        row["data_followup"] = agora_iso

        if resp.get("ok"):
            row["status_followup"] = "Enviado"
            enviados += 1
            tag = "DRY" if resp.get("dry_run") else "LIVE"
            log(f"[{tag}] ✅ follow-up → {nome} ({contato}): "
                f"{resp.get('status','?')}")
        else:
            row["status_followup"] = f"Falhou: {resp.get('status','?')}"
            log(f"❌ follow-up FALHOU → {nome} ({contato}): "
                f"{resp.get('status','?')}", "WARN")

        alterados = True

    if alterados:
        write_csv(PIPELINE_CSV, rows, PIPELINE_FIELDS)

    skip_summary = ", ".join(f"{k}={v}" for k, v in sorted(pulados.items())) or "—"
    log(f"Follow-up: enviados={enviados} | skipped: {skip_summary}", "INFO")
    print(f"FOLLOWUPS_ENVIADOS={enviados}")
    return enviados


if __name__ == "__main__":
    rodar()
