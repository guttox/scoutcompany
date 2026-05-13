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
  - Anti-spam: mesma pessoa não recebe mais de 1 resposta a cada 15s.
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
    LOG_DIR, PIPELINE_CSV, PIPELINE_FIELDS, add_numero_to_blacklist, env,
    is_numero_blacklisted, load_conversa, load_env, log, read_csv,
    save_conversa, send_whatsapp_via_evolution, write_csv,
)

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS = 350

ANTI_SPAM_SEGUNDOS = 15          # mesma pessoa não responde +1x a cada 15s
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

# ─── Rejeição AGRESSIVA (xingamento, ameaça, exaltação) ────────
# Match por substring lowercase. Não inclui rejeições neutras
# tipo "não quero" (essas caem em REJEICAO_EDUCADA).
PALAVRAS_REJEICAO_AGRESSIVA = [
    "vai se fud", "vai se foder", "vai tomar no", "vsf", "vtnc",
    "porra", "caralho", "merda", "filho da puta", "fdp",
    "encheu o saco", "saco cheio", "me deixa em paz",
    "para de me encher", "pare de me encher", "para de encher",
    "vai a merda", "vai à merda", "cala a boca",
    "idiota", "imbecil", "otario", "otário",
]

# ─── Rejeição EDUCADA (opt-out claro, sem agressão) ────────────
PALAVRAS_REJEICAO_EDUCADA = [
    "não tenho interesse", "nao tenho interesse",
    "sem interesse",
    "não preciso", "nao preciso",
    "não quero", "nao quero",
    "para de me mandar", "pare de me mandar",
    "para de mandar", "pare de mandar",
    "me tira dessa lista", "me tire dessa lista",
    "sair da lista", "remover da lista", "remove da lista",
    "isso é spam", "isto é spam", "é spam",
    "marcar como spam", "marcar spam",
    "vou bloquear", "vou te bloquear",
    "block", "blocked",
]

# Mensagens muito curtas que claramente significam parar.
# Comparadas após strip de pontuação contra a mensagem inteira.
PALAVRAS_REJEICAO_CURTAS = {
    "para", "pare", "chega", "ja chega", "já chega", "stop",
    "para com isso", "pare com isso", "parar", "para isso",
    "sai", "some", "nao", "não",  # "não" sozinho como resposta direta
}

# ─── Dúvida / hesitação (não é rejeição, é nudge) ──────────────
PALAVRAS_DUVIDA = [
    "não sei", "nao sei",
    "talvez", "quem sabe",
    "vou pensar", "preciso pensar", "vou ver",
    "está caro", "esta caro", "tá caro", "ta caro",
    "muito caro", "ficou caro",
    "não tenho dinheiro", "nao tenho dinheiro",
    "sem grana", "sem orçamento", "sem orcamento",
    "não dá agora", "nao da agora", "agora não", "agora nao",
]

# ─── Respostas padrão (texto do spec, não passa por Claude) ─────
RESPOSTA_REJEICAO_EDUCADA = (
    "Tudo bem, Leo da Scout entende!\n\n"
    "Não vou mais te incomodar por aqui.\n\n"
    "Se um dia precisar de site, sistema ou automação pode me chamar. "
    "Estarei por aqui.\n\n"
    "Boa sorte no negócio!"
)

RESPOSTA_REJEICAO_AGRESSIVA = (
    "Entendido, desculpa o incômodo!\n\n"
    "Não vou mais te contatar.\n\n"
    "Qualquer dia que precisar, a Scout está à disposição."
)

RESPOSTA_DUVIDA = (
    "Entendo completamente!\n\n"
    "Não precisa decidir agora. Se quiser tirar alguma dúvida antes ou "
    "entender melhor como funciona, pode me perguntar à vontade.\n\n"
    "Estou aqui quando precisar!"
)

SYSTEM_PROMPT = """Você é o Leo, assistente da Scout Company. Responda sempre em português brasileiro.

IDENTIDADE (siga à risca):
- Quando perguntarem quem é você: "Sou o Leo, da Scout!"
- Quando perguntarem se é IA: "Sou o assistente da Scout, aqui pra te ajudar!"
- Tom: profissional mas acessível. Simpático sem ser informal demais.

A Scout oferece 3 serviços. Identifique pela conversa qual o cliente precisa e foque nele:

1. SITES PROFISSIONAIS. Sites institucionais, landing pages e e-commerce.
   Entrega em 7 dias. Pra negócios que querem aparecer no Google e converter visitantes em clientes.
   Sinais de fit: "quero aparecer no Google", "não tenho site", "site antigo", "só tenho Instagram".

2. SISTEMAS DE GESTÃO. Sistemas customizados pra operações internas.
   Controle de clientes, agendamentos, financeiro, estoque, OS. Pra empresas que ainda usam papel/planilha.
   Sinais de fit: "controlo no caderno", "planilha do excel", clínicas, escolas, oficinas.

3. AUTOMAÇÃO COM IA. Prospecção automática, atendimento 24h via WhatsApp, geração de conteúdo.
   Pra empresas que querem crescer sem contratar mais gente.
   Sinais de fit: agências, consultorias, advocacia, corretoras, B2B, "preciso prospectar mais", "atendimento sobrecarregado".

Site da Scout: scoutcompany.com.br. Cada projeto é único. Sem mensalidade de plataforma. Suporte incluído após entrega.

PROIBIDO ABSOLUTO:
- Asterisco pra negrito: nada de *texto*. Tudo texto simples.
- Underline: nada de _texto_.
- Tachado: nada de ~texto~.
- Nome da empresa em destaque: escreve "Scout", nunca "*Scout*".
- Travessão (—) como separador. Use ponto, vírgula ou quebra de linha.
- Bullet points, listas, marcadores tipo "•", "-" ou numeração.
- Palavras excessivamente corporativas: "potencializar", "alavancar", "entregar valor",
  "no piloto automático", "agregar valor", "robusto", "performance", "engajamento".

REGRAS DE RESPOSTA:
- No máximo 4 parágrafos curtos. Em respostas simples, 1 ou 2 já bastam.
- Frases curtas e naturais, como gente escreve no WhatsApp.
- Profissional mas acessível. Direto ao ponto, explicativo quando precisar.
- Use no máximo 1 emoji por resposta.
- Identifique o serviço certo pela conversa. NÃO ofereça os 3 ao mesmo tempo.

CONDUÇÃO DA CONVERSA. Use a estrutura SPIN, em ordem:

1. SITUAÇÃO. Entenda o cenário atual.
   Ex.: "Você já tem site hoje?" / "Como você atrai clientes hoje?"

2. PROBLEMA. Identifique a dor.
   Ex.: "O que mais te incomoda no processo atual?" / "Você perde clientes por não aparecer no Google?"

3. IMPLICAÇÃO. Amplifique a dor.
   Ex.: "Imagina quanto cliente passa na frente do seu concorrente que aparece no Google e nem te acha..."

4. NECESSIDADE. Crie desejo pela solução.
   Ex.: "Se você tivesse um site que aparecesse no Google, como isso mudaria o seu negócio?"

REGRAS DE AVANÇO:
- Toda mensagem do Leo termina com UMA pergunta ou UM convite claro. Nunca deixe a conversa sem direção.
- UMA pergunta por mensagem. Nunca duas ou mais juntas.
- Se o cliente responder curto (sim, não, ok), aprofunde com uma pergunta de Implicação.
- Máximo 3 perguntas antes de apresentar a solução.
- Quando o cliente engajar bem com a dor e a necessidade, apresente o serviço com confiança e convide:
  "Posso te mostrar como funcionaria pro seu negócio numa conversa de 10 minutos?"
- Se o cliente sumir, não pressione. Leo só responde quando o cliente escreve de novo.

GATILHO DE LEAD QUENTE. Quando o cliente disser "topo", "fechado", "vou querer", "manda proposta", "quero fazer" ou equivalente, o sistema dispara alerta no Telegram automaticamente. A resposta do Leo nessa hora é a frase do bloco INTERESSE REAL EM CONTRATAR e ele PARA.

PROIBIDO NA CONDUÇÃO:
- Mais de 1 pergunta por mensagem.
- Apresentar os 3 serviços juntos (foque em UM, o que faz sentido pra dor identificada).
- Falar de preço (a regra geral vale aqui também).
- Mensagem sem direção: sempre termine com pergunta ou convite claro.

QUANDO O CLIENTE PERGUNTAR SOBRE UM SERVIÇO:
- Explica claro e simples, sem jargão.
- Usa exemplo concreto do segmento do cliente (ex.: "pra uma clínica, isso resolve...").
- Nunca deixa dúvida sem resposta.

SE NÃO ENTENDER A PERGUNTA:
"Posso te pedir pra reformular? Quero entender direito pra te ajudar."

PREÇO. NUNCA cite valores. Se perguntarem:
"Depende do que você precisa. Me conta um pouco mais sobre seu negócio que monto uma proposta personalizada."

EXEMPLOS, PORTFÓLIO OU PROJETOS ANTERIORES. NUNCA mande o cliente direto pro site da Scout esperando que ele veja projetos. Sempre redirecione a conversa pra entender o negócio dele primeiro e depois fale de algo específico. Nunca exponha que não tem portfólio público. Foque na dor e na solução personalizada. Mantenha a conversa fluindo natural.

- "Você tem exemplos?" ou "Já fez pra restaurante?":
  "Sim, já fizemos pra vários segmentos! Me conta mais sobre o seu negócio: qual o nome, o que você oferece e como atende hoje? Assim consigo te mostrar algo bem específico pro seu caso."

- Cliente insiste em ver portfólio:
  "Prefiro entender primeiro o que você precisa pra não te mostrar algo genérico. Me conta: qual é o maior problema que você quer resolver com o site, sistema ou automação?"

- "Vocês são experientes?":
  "Sim! Já trabalhamos com restaurantes, clínicas, salões, lojas e empresas B2B. Cada projeto é feito do zero pro negócio do cliente. Me conta o seu que te explico melhor como funcionaria."

INTERESSE REAL EM CONTRATAR. Quando o cliente demonstrar:
"Ótimo! Vou te conectar com nosso especialista pra alinhar os detalhes." E PARE de responder na próxima.

RESPOSTAS PRONTAS PRA SITUAÇÕES COMUNS:
- Perguntou prazo: "Site fica pronto em até 7 dias. Sistema e automação dependem do escopo. Pode me contar um pouco do que você precisa?"
- Não tem dinheiro agora: "Tudo bem, sem pressa. Quando fizer sentido pra você, é só me chamar."
- Como funciona: "Simples: você me conta o que precisa, conversamos rapidamente, e te apresento uma proposta personalizada."

NUNCA repita exatamente a mesma resposta. Adapte o tom à conversa."""


# ═══════════════════════════════════════════════════════════
# Anthropic API
# ═══════════════════════════════════════════════════════════
def _registrar_falha_resposta(detalhe):
    """Acrescenta linha em logs/falhas_resposta.log para auto-monitoramento."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "falhas_resposta.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {detalhe}\n")
    except Exception as e:
        log(f"não consegui gravar falhas_resposta.log: {e}", "ERROR")


def _gerar_resposta_claude(historico, mensagem_recebida):
    """Chama Claude com timeout de 30s + 1 retry. Em caso de falha total, devolve fallback específico."""
    try:
        import anthropic
    except ImportError:
        log("anthropic SDK ausente — use ./venv/bin/python", "ERROR")
        _registrar_falha_resposta("anthropic SDK ausente")
        return "Olá! Aqui é o Leo, da Scout. Recebi sua mensagem e respondo em instantes 😊"

    apikey = env("ANTHROPIC_API_KEY")
    if not apikey:
        log("ANTHROPIC_API_KEY ausente — fallback genérico", "ERROR")
        _registrar_falha_resposta("ANTHROPIC_API_KEY ausente")
        return "Olá! Aqui é o Leo, da Scout. Recebi sua mensagem e respondo em instantes 😊"

    # Monta histórico no formato Anthropic
    msgs = []
    for h in historico[-HISTORICO_LIMITE_CTX:]:
        role = "user" if h["role"] == "user" else "assistant"
        msgs.append({"role": role, "content": h["content"]})
    msgs.append({"role": "user", "content": mensagem_recebida})

    client = anthropic.Anthropic(api_key=apikey, timeout=30.0)
    last_err = None
    for attempt in (1, 2):
        try:
            resp = client.messages.create(
                model=env("ANTHROPIC_MODEL", ANTHROPIC_MODEL),
                max_tokens=ANTHROPIC_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=msgs,
            )
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    return block.text.strip()
            return ""
        except Exception as e:
            last_err = e
            log(f"Claude tentativa {attempt}/2 falhou: {e}", "ERROR")
            _registrar_falha_resposta(f"tentativa {attempt}/2: {type(e).__name__}: {e}")
            if attempt < 2:
                time.sleep(1.5)

    _registrar_falha_resposta(f"esgotou retries — último erro: {last_err}")
    return "Oi! Tive um problema técnico aqui. Pode repetir sua última mensagem? 😊"


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
# Detector REJEIÇÃO (educada vs agressiva)
# ═══════════════════════════════════════════════════════════
def _normaliza_curto(texto):
    """Limpa pontuação de uma mensagem curta pra match exato em PALAVRAS_REJEICAO_CURTAS."""
    if not texto:
        return ""
    t = texto.lower().strip()
    for ch in "!?.,;:\"'\n\r":
        t = t.replace(ch, " ")
    return " ".join(t.split())


def detectar_rejeicao(texto):
    """Retorna (tipo, palavra) onde tipo é 'agressiva', 'educada' ou None."""
    if not texto:
        return (None, None)
    t = texto.lower()

    for kw in PALAVRAS_REJEICAO_AGRESSIVA:
        if kw in t:
            return ("agressiva", kw)

    for kw in PALAVRAS_REJEICAO_EDUCADA:
        if kw in t:
            return ("educada", kw)

    # Mensagens muito curtas tipo "para", "chega", "stop" — só viram rejeição
    # se a mensagem inteira (após limpar pontuação) for a palavra.
    curta = _normaliza_curto(texto)
    if curta in PALAVRAS_REJEICAO_CURTAS:
        return ("educada", curta)

    return (None, None)


# ═══════════════════════════════════════════════════════════
# Detector DÚVIDA / hesitação
# ═══════════════════════════════════════════════════════════
def detectar_duvida(texto):
    if not texto:
        return None
    t = texto.lower()
    for kw in PALAVRAS_DUVIDA:
        if kw in t:
            return kw
    return None


def marcar_rejeitado_pipeline(numero):
    """Marca status='Rejeitado' na linha do pipeline cujo contato bate com o número.
    Retorna nome do prospect (ou None se não estava na pipeline)."""
    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        return None
    digits = "".join(c for c in str(numero) if c.isdigit())
    nome_match = None
    for row in pipeline:
        contato = "".join(c for c in (row.get("contato") or "") if c.isdigit())
        if contato and (contato in digits or digits in contato):
            row["status"] = "Rejeitado"
            row["observacao"] = (row.get("observacao") or "").strip()
            if row["observacao"]:
                row["observacao"] += " | "
            row["observacao"] += f"rejeitado em {datetime.now().isoformat(timespec='seconds')}"
            nome_match = row.get("nome", "")
            break
    if nome_match:
        write_csv(PIPELINE_CSV, pipeline, PIPELINE_FIELDS)
    return nome_match


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
    now_iso = datetime.now().isoformat(timespec="seconds")

    def _append_user_msg():
        conversa["mensagens"].append({
            "role": "user", "content": texto_recebido, "ts": now_iso,
        })

    # 0. BLACKLIST / REJEITADO — silêncio total. Nunca mais responde.
    if is_numero_blacklisted(numero) or conversa.get("rejeitado"):
        log(f"[{numero}] blacklisted/rejeitado — silenciando", "INFO")
        _append_user_msg()
        save_conversa(numero, conversa)
        return {"sent": False, "reason": "blacklisted", "lead_quente": False}

    # 1. Se já marcado como lead quente, não responde mais — humano assume
    if conversa.get("lead_quente"):
        log(f"[{numero}] lead quente — silenciando", "INFO")
        _append_user_msg()
        save_conversa(numero, conversa)
        return {"sent": False, "reason": "lead_quente_silencio", "lead_quente": True}

    # 2. REJEIÇÃO — detectada ANTES de anti-spam (sempre honra o pedido de parada)
    tipo_rej, palavra_rej = detectar_rejeicao(texto_recebido)
    if tipo_rej:
        _append_user_msg()
        resposta_rej = (RESPOSTA_REJEICAO_AGRESSIVA if tipo_rej == "agressiva"
                        else RESPOSTA_REJEICAO_EDUCADA)
        send_resp = send_whatsapp_via_evolution(numero, resposta_rej)
        if send_resp.get("ok"):
            conversa["mensagens"].append({
                "role": "assistant", "content": resposta_rej,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "dry_run": send_resp.get("dry_run", False),
            })
        # Marca tudo e blacklist DEPOIS do envio (mesmo se falhou, ainda blacklista)
        conversa["rejeitado"] = True
        conversa["rejeicao_tipo"] = tipo_rej
        conversa["rejeicao_palavra"] = palavra_rej
        conversa["rejeitado_em"] = datetime.now().isoformat(timespec="seconds")
        save_conversa(numero, conversa)
        nome_pipeline = marcar_rejeitado_pipeline(numero)
        add_numero_to_blacklist(numero, motivo=f"rejeicao_{tipo_rej}:{palavra_rej}")
        log(f"🚫 [{numero}] REJEIÇÃO {tipo_rej.upper()} ('{palavra_rej}') — "
            f"prospect={nome_pipeline or '(novo)'} • blacklisted")
        return {"sent": send_resp.get("ok", False),
                "reason": f"rejeicao_{tipo_rej}",
                "lead_quente": False,
                "blacklisted": True,
                "resposta": resposta_rej}

    # 3. Anti-spam: respondi essa pessoa há menos de 15s?
    ultimas = [m for m in conversa.get("mensagens", []) if m.get("role") == "assistant"]
    if ultimas:
        try:
            ts_ultima = datetime.fromisoformat(ultimas[-1]["ts"])
            if (datetime.now() - ts_ultima).total_seconds() < ANTI_SPAM_SEGUNDOS:
                log(f"[{numero}] anti-spam: respondi há <{ANTI_SPAM_SEGUNDOS}s, skip", "INFO")
                _append_user_msg()
                save_conversa(numero, conversa)
                return {"sent": False, "reason": "anti_spam", "lead_quente": False}
        except Exception:
            pass

    # 4. Adiciona a recebida ao histórico
    _append_user_msg()

    # 5. Detecta lead quente PRIMEIRO (não responde, só alerta)
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

    # 6. DÚVIDA / hesitação — responde uma vez com texto de spec, sem chamar Claude.
    #    Subsequentes msgs caem no fluxo normal (Claude decide).
    duvida_kw = detectar_duvida(texto_recebido)
    if duvida_kw and not conversa.get("duvida_handled"):
        send_resp = send_whatsapp_via_evolution(numero, RESPOSTA_DUVIDA)
        if send_resp.get("ok"):
            conversa["mensagens"].append({
                "role": "assistant", "content": RESPOSTA_DUVIDA,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "dry_run": send_resp.get("dry_run", False),
            })
        conversa["duvida_handled"] = True
        save_conversa(numero, conversa)
        log(f"💭 [{numero}] dúvida detectada ('{duvida_kw}') — resposta padrão")
        return {"sent": send_resp.get("ok", False), "reason": "duvida",
                "lead_quente": False, "resposta": RESPOSTA_DUVIDA}

    # 7. Delay humano 5-15s
    wait = random.randint(DELAY_MIN_SEG, DELAY_MAX_SEG)
    log(f"[{numero}] respondendo em {wait}s")
    time.sleep(wait)

    # 8. Chama Claude
    historico_pra_claude = conversa.get("mensagens", [])[:-1]  # sem a msg que acabou de chegar
    resposta = _gerar_resposta_claude(historico_pra_claude, texto_recebido)
    if not resposta:
        log(f"[{numero}] Claude devolveu vazio — abortando", "ERROR")
        save_conversa(numero, conversa)
        return {"sent": False, "reason": "claude_vazio", "lead_quente": False}

    # 9. Envia via Evolution (respeita SCOUT_DRY_RUN)
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
        rej_tipo, rej_kw = detectar_rejeicao(args.detectar)
        duv = detectar_duvida(args.detectar)
        print(json.dumps({
            "lead_quente": bool(kw), "intencao": kw,
            "rejeicao_tipo": rej_tipo, "rejeicao_palavra": rej_kw,
            "duvida": duv,
        }, ensure_ascii=False))
        sys.exit(0)

    if args.test:
        out = responder_mensagem(args.numero, args.test, nome_pushname="Teste CLI")
        print(json.dumps(out, ensure_ascii=False, indent=2))
