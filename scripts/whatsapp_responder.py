"""
Agente IA da Scout que responde mensagens recebidas no WhatsApp via Evolution.

Funções principais:
  - responder_mensagem(numero, texto_recebido, nome_pushname=None)
      Chama Claude para gerar resposta, respeitando anti-spam, histórico
      por número, e detecção de lead quente.

Comportamento:
  - Tom: simpático, direto, profissional. Português BR.
  - Nunca revela preço — sempre redireciona.
  - Se detectar palavra-chave de intenção → ALERTA Telegram + para de responder.
  - Anti-spam: mesma pessoa não recebe mais de 1 resposta por minuto.
  - Delay 5-15s antes de cada resposta (parece humano).
  - Histórico até 10 mensagens em ~/scout/conversas/[numero].json.

Não roda direto — é importado pelo webhook_server.py.
Pode rodar standalone só pra testar prompt:
  python3 whatsapp_responder.py --test "Quanto custa?"
"""
import argparse
import json
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    PIPELINE_CSV, PIPELINE_FIELDS, env, load_env, load_conversa, log,
    read_csv, save_conversa, send_whatsapp_via_evolution, write_csv,
)

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS = 350

ANTI_SPAM_SEGUNDOS = 60          # mesma pessoa não responde +1x por minuto
DELAY_MIN_SEG = 5                # delay humano antes de responder
DELAY_MAX_SEG = 15
HISTORICO_LIMITE_CTX = 10        # quantas msgs anteriores mandar pro Claude

# Palavras-chave de lead quente (lowercase, match por substring)
PALAVRAS_LEAD_QUENTE = [
    "quero fazer", "vamos fazer", "fechado", "quanto fica",
    "como contrato", "como contrata", "aceito", "topei", "topo",
    "me manda proposta", "manda proposta", "quero contratar",
    "bora fazer", "pode fazer", "vou querer",
]

SYSTEM_PROMPT = """Você é o assistente da Scout Company. Responda sempre em português brasileiro.

A Scout desenvolve sites, sistemas e automação para negócios que querem crescer online.
Entrega em até 7 dias. Cada projeto é único e personalizado. Site: scoutcompany.com.br.
Sem mensalidade de plataforma. Suporte incluído após entrega.

REGRAS:
- Tom: simpático, direto e profissional. Frases curtas. Use no máximo 1 emoji por resposta.
- Nunca revele que é IA. Se perguntarem se é IA: "Sou o assistente da Scout, aqui pra te ajudar!"
- NUNCA mencione preços. Se perguntarem quanto custa: "Depende muito do que você precisa! Me conta mais sobre seu negócio que monto uma proposta personalizada 😊"
- Quando cliente demonstrar interesse real em contratar: "Ótimo! Deixa eu te conectar com nosso especialista para alinhar os detalhes 😊" — e PARE de responder na próxima mensagem.

RESPOSTAS PRONTAS PRA SITUAÇÕES COMUNS:
- Pediu exemplo de trabalho: "Acessa scoutcompany.com.br — tem nossos projetos lá. O que achou?"
- Perguntou prazo: "Entregamos em até 7 dias após aprovação do briefing."
- Disse que não tem dinheiro: "Entendo! Quando fizer sentido financeiramente pode me chamar 😊"
- Perguntou como funciona: "Simples: você me conta o que precisa, a gente conversa rapidinho e entrego em até 7 dias."

NUNCA repita exatamente a mesma mensagem da última resposta. Adapte o tom à conversa.
Responda em no máximo 3 frases. Direto ao ponto."""


# ═══════════════════════════════════════════════════════════
# Anthropic API
# ═══════════════════════════════════════════════════════════
def _gerar_resposta_claude(historico, mensagem_recebida):
    """Chama Claude e devolve só o texto. Em caso de erro, fallback genérico."""
    try:
        import anthropic
    except ImportError:
        log("anthropic SDK ausente — use ./venv/bin/python", "ERROR")
        return "Olá! Recebi sua mensagem. Em breve um dos nossos consultores responde 😊"

    apikey = env("ANTHROPIC_API_KEY")
    if not apikey:
        log("ANTHROPIC_API_KEY ausente — fallback genérico", "ERROR")
        return "Olá! Recebi sua mensagem. Em breve um dos nossos consultores responde 😊"

    # Monta histórico no formato Anthropic
    msgs = []
    for h in historico[-HISTORICO_LIMITE_CTX:]:
        role = "user" if h["role"] == "user" else "assistant"
        msgs.append({"role": role, "content": h["content"]})
    msgs.append({"role": "user", "content": mensagem_recebida})

    try:
        client = anthropic.Anthropic(api_key=apikey)
        resp = client.messages.create(
            model=env("ANTHROPIC_MODEL", ANTHROPIC_MODEL),
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=msgs,
        )
        # resp.content é uma lista de blocks; pega texto
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""
    except Exception as e:
        log(f"Claude falhou: {e}", "ERROR")
        return "Tive um soluço aqui, posso te chamar de volta em 1 min? 😊"


# ═══════════════════════════════════════════════════════════
# Detector lead quente
# ═══════════════════════════════════════════════════════════
def detectar_lead_quente(texto):
    if not texto:
        return None
    t = texto.lower()
    for kw in PALAVRAS_LEAD_QUENTE:
        if kw in t:
            return kw
    return None


# ═══════════════════════════════════════════════════════════
# Alerta Telegram
# ═══════════════════════════════════════════════════════════
def alertar_telegram_lead_quente(nome, numero, ultima_msg, intencao):
    token = env("TELEGRAM_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("TELEGRAM_TOKEN/CHAT_ID ausentes — não consigo alertar", "ERROR")
        return False
    link_wpp = f"https://wa.me/{numero}"
    texto = (
        f"🔥 LEAD QUENTE — ASSUMA AGORA!\n\n"
        f"🏪 Empresa: {nome or '(não identificada)'}\n"
        f"📱 WhatsApp: {numero}\n"
        f"💬 Última mensagem: {ultima_msg[:300]}\n"
        f"🎯 Intenção: {intencao}\n\n"
        f"👆 Entre no WhatsApp e feche!\n{link_wpp}"
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": texto,
        "disable_web_page_preview": "false",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=payload, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"alerta Telegram falhou: {e}", "ERROR")
        return False


def marcar_lead_quente_pipeline(numero):
    """Se prospect com esse número está na pipeline, marca como 'Lead Quente'.
    Retorna nome encontrado (ou None)."""
    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        return None
    digits = "".join(c for c in str(numero) if c.isdigit())
    nome_match = None
    for row in pipeline:
        contato = "".join(c for c in (row.get("contato") or "") if c.isdigit())
        if contato and contato in digits or digits in contato:
            row["status"] = "Lead Quente"
            nome_match = row.get("nome", "")
            break
    if nome_match:
        write_csv(PIPELINE_CSV, pipeline, PIPELINE_FIELDS)
    return nome_match


def lookup_nome_pipeline(numero):
    """Acha o nome associado ao número (se o prospect veio da pipeline Scout)."""
    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        return None
    digits = "".join(c for c in str(numero) if c.isdigit())
    for row in pipeline:
        contato = "".join(c for c in (row.get("contato") or "") if c.isdigit())
        if contato and (contato in digits or digits in contato):
            return row.get("nome", "")
    return None


# ═══════════════════════════════════════════════════════════
# Pipeline principal (chamado pelo webhook_server)
# ═══════════════════════════════════════════════════════════
def responder_mensagem(numero, texto_recebido, nome_pushname=None):
    """Processa 1 mensagem recebida.

    Retorna dict com:
      sent: bool — se enviou resposta
      reason: str — explicação
      lead_quente: bool
    """
    load_env()
    if not texto_recebido or not numero:
        return {"sent": False, "reason": "vazio", "lead_quente": False}

    conversa = load_conversa(numero)

    # 1. Se já marcado como lead quente, não responde mais — humano assume
    if conversa.get("lead_quente"):
        log(f"[{numero}] lead quente — silenciando", "INFO")
        # ainda salva a msg recebida no histórico
        conversa["mensagens"].append({
            "role": "user", "content": texto_recebido,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })
        save_conversa(numero, conversa)
        return {"sent": False, "reason": "lead_quente_silencio", "lead_quente": True}

    # 2. Anti-spam: respondi essa pessoa há menos de 60s?
    ultimas = [m for m in conversa.get("mensagens", []) if m.get("role") == "assistant"]
    if ultimas:
        try:
            ts_ultima = datetime.fromisoformat(ultimas[-1]["ts"])
            if (datetime.now() - ts_ultima).total_seconds() < ANTI_SPAM_SEGUNDOS:
                log(f"[{numero}] anti-spam: respondi há <{ANTI_SPAM_SEGUNDOS}s, skip", "INFO")
                # ainda grava a recebida
                conversa["mensagens"].append({
                    "role": "user", "content": texto_recebido,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                })
                save_conversa(numero, conversa)
                return {"sent": False, "reason": "anti_spam", "lead_quente": False}
        except Exception:
            pass

    # 3. Adiciona a recebida ao histórico
    conversa["mensagens"].append({
        "role": "user", "content": texto_recebido,
        "ts": datetime.now().isoformat(timespec="seconds"),
    })

    # 4. Detecta lead quente PRIMEIRO (não responde, só alerta)
    intencao = detectar_lead_quente(texto_recebido)
    if intencao:
        nome = lookup_nome_pipeline(numero) or nome_pushname or "(novo contato)"
        marcar_lead_quente_pipeline(numero)
        conversa["lead_quente"] = True
        conversa["lead_quente_em"] = datetime.now().isoformat(timespec="seconds")
        alertar_telegram_lead_quente(nome, numero, texto_recebido, intencao)
        log(f"🔥 [{numero}] LEAD QUENTE detectado: '{intencao}' — alerta enviado")
        save_conversa(numero, conversa)
        return {"sent": False, "reason": "lead_quente_handoff", "lead_quente": True}

    # 5. Delay humano 5-15s
    wait = random.randint(DELAY_MIN_SEG, DELAY_MAX_SEG)
    log(f"[{numero}] respondendo em {wait}s")
    time.sleep(wait)

    # 6. Chama Claude
    historico_pra_claude = conversa.get("mensagens", [])[:-1]  # sem a msg que acabou de chegar
    resposta = _gerar_resposta_claude(historico_pra_claude, texto_recebido)
    if not resposta:
        log(f"[{numero}] Claude devolveu vazio — abortando", "ERROR")
        save_conversa(numero, conversa)
        return {"sent": False, "reason": "claude_vazio", "lead_quente": False}

    # 7. Envia via Evolution (respeita SCOUT_DRY_RUN)
    send_resp = send_whatsapp_via_evolution(numero, resposta)
    if send_resp["ok"]:
        conversa["mensagens"].append({
            "role": "assistant", "content": resposta,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "dry_run": send_resp.get("dry_run", False),
        })
    save_conversa(numero, conversa)
    return {"sent": send_resp["ok"], "reason": send_resp["status"],
            "lead_quente": False, "resposta": resposta}


# ═══════════════════════════════════════════════════════════
# CLI de teste
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=str, help="texto pra simular recebida")
    parser.add_argument("--numero", type=str, default="5511999999999")
    parser.add_argument("--detectar", type=str, help="testa só detector lead quente")
    args = parser.parse_args()

    if args.detectar:
        kw = detectar_lead_quente(args.detectar)
        print(json.dumps({"lead_quente": bool(kw), "intencao": kw}, ensure_ascii=False))
        sys.exit(0)

    if args.test:
        out = responder_mensagem(args.numero, args.test, nome_pushname="Teste CLI")
        print(json.dumps(out, ensure_ascii=False, indent=2))
