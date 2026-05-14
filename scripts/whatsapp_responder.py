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
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    DATA_DIR, LOG_DIR, PIPELINE_CSV, PIPELINE_FIELDS, add_numero_to_blacklist,
    env, is_numero_blacklisted, load_conversa, load_env, log, read_csv,
    save_conversa, send_whatsapp_via_evolution, write_csv,
)

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS = 350

ANTI_SPAM_SEGUNDOS = 5           # só bloqueia repetição EXATA da última msg do user em <5s
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

# ─── Pergunta de preço direta (curta, sem contexto) ───────────
# Cliente que manda só "Valores" / "Quanto custa?" antes de qualquer conversa
# recebe template fixo (com nome do segmento) e SPIN começa naturalmente.
PALAVRAS_PRECO_DIRETO = [
    "valores", "valor", "preço", "preco", "preços", "precos",
    "quanto custa", "quanto fica", "quanto sai", "quanto é",
    "qual o valor", "qual valor", "qual o preço", "qual preco",
    "tabela", "tabela de preço", "tabela de preco",
    "orçamento", "orcamento", "ficou quanto",
]

# Artigo + pronome por segmento (mesmo "seu/sua" muda gênero em PT-BR).
SEGMENTO_FRASE = {
    "restaurante": "o seu restaurante",
    "pizzaria": "a sua pizzaria",
    "lanchonete": "a sua lanchonete",
    "padaria": "a sua padaria",
    "sorveteria": "a sua sorveteria",
    "doceria": "a sua doceria",
    "confeitaria": "a sua confeitaria",
    "açougue": "o seu açougue",
    "acougue": "o seu açougue",
    "hamburgueria": "a sua hamburgueria",
    "clínica": "a sua clínica",
    "clinica": "a sua clínica",
    "consultório": "o seu consultório",
    "consultorio": "o seu consultório",
    "farmácia": "a sua farmácia",
    "farmacia": "a sua farmácia",
    "academia": "a sua academia",
    "estúdio": "o seu estúdio",
    "estudio": "o seu estúdio",
    "loja": "a sua loja",
    "barbearia": "a sua barbearia",
    "salão": "o seu salão",
    "salao": "o seu salão",
    "escritório": "o seu escritório",
    "escritorio": "o seu escritório",
    "oficina": "a sua oficina",
    "imobiliária": "a sua imobiliária",
    "imobiliaria": "a sua imobiliária",
    "petshop": "o seu petshop",
    "pet shop": "o seu petshop",
    "hotel": "o seu hotel",
    "pousada": "a sua pousada",
    "agência": "a sua agência",
    "agencia": "a sua agência",
}


def detectar_preco_direto(texto):
    """True se a mensagem é curta e bate só com pergunta de preço."""
    if not texto:
        return False
    t = texto.lower().strip()
    for ch in "!?.,;:":
        t = t.replace(ch, "")
    t = " ".join(t.split())
    if len(t) > 30:
        return False
    return any(p in t for p in PALAVRAS_PRECO_DIRETO)


def _frase_segmento(seg):
    if not seg:
        return "o seu negócio"
    s = seg.lower().strip()
    if s in SEGMENTO_FRASE:
        return SEGMENTO_FRASE[s]
    for k, v in SEGMENTO_FRASE.items():
        if k in s:
            return v
    return "o seu negócio"


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

# ═══════════════════════════════════════════════════════════
# DETECÇÃO DE BOT / WHATSAPP BUSINESS / CARDÁPIO AUTOMÁTICO
# ═══════════════════════════════════════════════════════════
BOTS_CONHECIDOS_PATH = DATA_DIR / "bots_conhecidos.txt"

DEDUP_RESPOSTA_JANELA_SEG = 60     # janela do dedup por similaridade
DEDUP_RESPOSTA_LIMIAR = 0.70       # ratio mínimo do SequenceMatcher pra considerar duplicada
LOOP_MENSAGENS_IDENTICAS = 3     # 3+ idênticas em LOOP_JANELA_SEG = bot
LOOP_JANELA_SEG = 120

# URLs típicas de bot de delivery / cardápio digital
BOT_URL_PATTERNS = [
    "ifood.com", "rappi.com", "uber.com", "ubereats.com",
    "aiqfome.com", "mykeeta.com", "keeta.com", "99food", "99app.com",
    "anota.ai", "goomer.app", "neemo.com.br", "delivery.much.com",
]

# Frases recorrentes de WhatsApp Business / atendimento automático
BOT_FRASES = [
    "agradece seu contato", "agradecemos seu contato", "agradecemos o contato",
    "como podemos ajudar?", "como podemos te ajudar",
    "pedido automático", "pedido automatico",
    "resposta automática", "resposta automatica",
    "mensagem automática", "mensagem automatica",
    "atendimento automático", "atendimento automatico",
    "somente via", "apenas via", "pedidos somente",
    "horário de funcionamento", "horario de funcionamento",
    "horário de atendimento", "horario de atendimento",
    "cardápio digital", "cardapio digital", "nosso cardápio", "nosso cardapio",
    "faça seu pedido", "faca seu pedido",
]

# Emojis estruturados típicos de menus/bots
BOT_EMOJIS_ESTRUTURADOS = ["🛵", "✅", "📋", "📲", "🍔", "🍕", "📦",
                            "🛒", "🏪", "📞", "🕐", "🕒", "📍", "🔔"]

PRICE_REGEX = re.compile(r"R\$\s*\d+[,\.]\d{2}")
URL_REGEX = re.compile(r"https?://\S+|www\.\S+")
EMOJI_REGEX = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]"
)

# Motivos "soft": ignora a mensagem mas NÃO marca o número como bot
# (poderia ser um humano mandando "Oi" ou só um link colado por engano).
SOFT_BOT_MOTIVOS = {"muito_curta", "so_emojis", "so_link"}


def detectar_bot_whatsapp(texto):
    """Retorna motivo (str) se a mensagem parece bot/cardápio/auto, senão None."""
    if not texto:
        return None
    t = texto.strip()
    t_lower = t.lower()

    if len(t) < 3:
        return "muito_curta"

    for url in BOT_URL_PATTERNS:
        if url in t_lower:
            return f"url_delivery:{url}"

    for frase in BOT_FRASES:
        if frase in t_lower:
            return f"frase_auto:{frase[:40]}"

    if len(PRICE_REGEX.findall(t)) >= 2:
        return "cardapio_precos"

    sem_url = URL_REGEX.sub("", t).strip()
    if URL_REGEX.search(t) and len(sem_url) < 3:
        return "so_link"

    sem_emoji = EMOJI_REGEX.sub("", t).strip()
    if not sem_emoji:
        return "so_emojis"

    inicio = t[:30]
    if sum(1 for e in BOT_EMOJIS_ESTRUTURADOS if e in inicio) >= 2:
        return "emojis_estruturados_inicio"
    if len(EMOJI_REGEX.findall(inicio)) >= 3:
        return "muitos_emojis_inicio"

    return None


def _bot_conhecido(numero):
    digits = "".join(c for c in str(numero) if c.isdigit())
    if not digits or not BOTS_CONHECIDOS_PATH.exists():
        return False
    for raw in BOTS_CONHECIDOS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        n_digits = "".join(c for c in line.split("|")[0] if c.isdigit())
        if n_digits and n_digits == digits:
            return True
    return False


def _registrar_bot(numero, motivo):
    digits = "".join(c for c in str(numero) if c.isdigit())
    if not digits or _bot_conhecido(numero):
        return
    BOTS_CONHECIDOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not BOTS_CONHECIDOS_PATH.exists():
        BOTS_CONHECIDOS_PATH.write_text(
            "# Scout — números detectados como bots/automação. Não responder.\n"
            "# Formato: numero | motivo | timestamp\n",
            encoding="utf-8",
        )
    with open(BOTS_CONHECIDOS_PATH, "a", encoding="utf-8") as f:
        f.write(f"{digits} | {motivo} | "
                f"{datetime.now().isoformat(timespec='seconds')}\n")


def _detectar_loop(conversa, texto_recebido):
    """3+ msgs idênticas do user em LOOP_JANELA_SEG (inclui msg atual na contagem)."""
    if not texto_recebido:
        return False
    cutoff = datetime.now() - timedelta(seconds=LOOP_JANELA_SEG)
    norm = texto_recebido.strip().lower()
    count = 1  # mensagem que acabou de chegar
    for m in conversa.get("mensagens", []):
        if m.get("role") != "user":
            continue
        if (m.get("content") or "").strip().lower() != norm:
            continue
        try:
            ts = datetime.fromisoformat(m["ts"])
        except Exception:
            continue
        if ts >= cutoff:
            count += 1
            if count >= LOOP_MENSAGENS_IDENTICAS:
                return True
    return False


def _resposta_duplicada(conversa, resposta_proposta):
    """True se a resposta proposta é >= DEDUP_RESPOSTA_LIMIAR similar a
    alguma resposta enviada nos últimos DEDUP_RESPOSTA_JANELA_SEG.

    Match exato é caso particular (ratio=1.0). difflib.SequenceMatcher pega
    despedidas e variações com mesma intenção mas wording levemente diferente
    (ex.: 'Tudo bem, Leo entende!' vs 'Entendido, sem problema!')."""
    norm = (resposta_proposta or "").strip().lower()
    if not norm:
        return False
    cutoff = datetime.now() - timedelta(seconds=DEDUP_RESPOSTA_JANELA_SEG)
    for m in conversa.get("mensagens", []):
        if m.get("role") != "assistant":
            continue
        try:
            ts = datetime.fromisoformat(m["ts"])
        except Exception:
            continue
        if ts < cutoff:
            continue
        prev = (m.get("content") or "").strip().lower()
        if not prev:
            continue
        if prev == norm:
            return True
        if SequenceMatcher(None, prev, norm).ratio() >= DEDUP_RESPOSTA_LIMIAR:
            return True
    return False


def _marcar_bot_pipeline(numero, motivo):
    """Marca status='Bot detectado' na linha do pipeline cujo contato bate."""
    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        return None
    digits = "".join(c for c in str(numero) if c.isdigit())
    nome_match = None
    for row in pipeline:
        contato = "".join(c for c in (row.get("contato") or "") if c.isdigit())
        if contato and (contato in digits or digits in contato):
            row["status"] = "Bot detectado"
            obs = (row.get("observacao") or "").strip()
            if obs:
                obs += " | "
            obs += f"bot {motivo} em {datetime.now().isoformat(timespec='seconds')}"
            row["observacao"] = obs
            nome_match = row.get("nome", "")
            break
    if nome_match:
        write_csv(PIPELINE_CSV, pipeline, PIPELINE_FIELDS)
    return nome_match


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

# ─── Inbound cold welcome — cliente que chega frio (sem prospecção) ──
# Resposta determinística pra primeira mensagem de quem nunca recebeu
# nossa prospecção. Apresenta os 3 serviços de forma fluida (sem bullets,
# sem travessão) e abre o SPIN com pergunta de Situação. Zero token Claude.
RESPOSTA_INBOUND_COLD = (
    "Olá! Aqui é o Leo, da Scout 👋\n\n"
    "A gente ajuda negócios a crescerem com tecnologia em três frentes: "
    "sites profissionais pra quem quer aparecer no Google e converter "
    "visitantes, sistemas de gestão pra empresas que ainda controlam "
    "tudo em papel ou planilha, e automação com IA pra atendimento 24h "
    "e prospecção que roda sem você precisar acompanhar.\n\n"
    "Pra eu te direcionar bem, me conta: qual é o seu negócio e o que "
    "você está buscando resolver?"
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
- Tom: profissional E humano. Simpático sem ser informal demais. NUNCA robótico, NUNCA corporativo demais.

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

PROIBIDO ABSOLUTO (essas regras NÃO PODEM ser quebradas em hipótese alguma):
- Asterisco pra negrito: nada de *texto*. Tudo texto simples.
- Underline: nada de _texto_.
- Tachado: nada de ~texto~.
- Nome da empresa em destaque: escreve "Scout", nunca "*Scout*".
- Travessão (— ou –) em QUALQUER posição da mensagem. NUNCA use travessão como separador, nem entre frases, nem em apostos. Substitua sempre por ponto, vírgula, dois pontos ou quebra de linha. Se você foi tentado a escrever travessão, reescreva a frase.
- Hífen no meio de frase como pausa (ex.: "Scout - agência de tráfego"). Use vírgula, ponto ou quebra de linha.
- Bullet points, listas numeradas, marcadores tipo "•", "-", "*" ou "1." em qualquer mensagem. Escreva em parágrafos contínuos.
- Palavras corporativas: "solução", "potencializar", "alavancar", "entregar valor",
  "no piloto automático", "agregar valor", "robusto", "performance", "engajamento",
  "sinergia", "ecossistema", "disrupção", "escalável". Use linguagem simples e direta.

REGRAS DE RESPOSTA (também inegociáveis):
- NO MÁXIMO 4 parágrafos curtos. Em respostas simples, 1 ou 2 já bastam. Mensagem longa demais quebra a conversa no WhatsApp.
- Frases curtas e naturais, como gente escreve no WhatsApp. Nada de frases com mais de 25 palavras.
- Profissional E humano. Direto ao ponto, explicativo quando precisar. Nunca robótico, nunca formal demais.
- Use no máximo 1 emoji por resposta. Pode até não usar nenhum.
- Identifique o serviço certo pela conversa. NÃO ofereça os 3 ao mesmo tempo.

CONDUÇÃO DA CONVERSA. Use a estrutura SPIN, em ordem:

1. SITUAÇÃO. Entenda o cenário atual.
   Ex.: "Você já tem site hoje?" / "Como você atrai clientes hoje?"

2. PROBLEMA. Identifique a dor.
   Ex.: "O que mais te incomoda no processo atual?" / "Você perde clientes por não aparecer no Google?"

3. IMPLICAÇÃO. Amplifique a dor.
   Ex.: "Imagina quanto cliente passa na frente do seu concorrente que aparece no Google e nem te acha..."

4. NECESSIDADE. Crie desejo pelo serviço (sem usar a palavra "solução").
   Ex.: "Se você tivesse um site que aparecesse no Google, como isso mudaria o seu negócio?"

REGRAS DE AVANÇO:
- Toda mensagem do Leo termina com UMA pergunta ou UM convite claro. Nunca deixe a conversa sem direção.
- UMA pergunta por mensagem. Nunca duas ou mais juntas.
- Se o cliente responder curto (sim, não, ok), aprofunde com uma pergunta de Implicação.
- Máximo 3 perguntas antes de apresentar o serviço.
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

EXEMPLOS, PORTFÓLIO OU PROJETOS ANTERIORES. NUNCA mande o cliente direto pro site da Scout esperando que ele veja projetos. Sempre redirecione a conversa pra entender o negócio dele primeiro e depois fale de algo específico. Nunca exponha que não tem portfólio público. Foque na dor e na proposta personalizada. Mantenha a conversa fluindo natural.

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
                # Prompt caching no SYSTEM_PROMPT — ~3K tokens estáveis.
                # 1ª chamada paga 1.25x (write), próximas dentro de 5min pagam 0.1x.
                # Em horário de pico (várias mensagens em <5min), economia ~70% do
                # input. Sem isso, cada call pagava o prompt inteiro full price.
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=msgs,
            )
            cache_hit = getattr(resp.usage, "cache_read_input_tokens", 0)
            cache_write = getattr(resp.usage, "cache_creation_input_tokens", 0)
            if cache_hit or cache_write:
                log(f"claude usage: input={resp.usage.input_tokens} "
                    f"cache_read={cache_hit} cache_write={cache_write} "
                    f"output={resp.usage.output_tokens}", "INFO")
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


def _is_inbound_cold(numero, conversa):
    """True se é o PRIMEIRO contato com esse número E ele não veio da nossa
    prospecção (sem data_envio_site na pipeline). Cliente que chegou frio
    via WhatsApp sem nunca ter recebido nossa abordagem.

    Casos cobertos:
      - Conversa sem nenhuma msg de assistant ainda
      - Pipeline sem entry pro número (descoberta orgânica) → inbound
      - Pipeline com entry mas sem data_envio_site (status Novo, nunca
        prospectamos) → inbound
      - Pipeline com data_envio_site preenchido (nós mandamos prospecção) →
        NÃO é cold, ele está respondendo nosso pitch
    """
    for m in conversa.get("mensagens", []):
        if m.get("role") == "assistant":
            return False
    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        return True
    digits = "".join(c for c in str(numero) if c.isdigit())
    for row in pipeline:
        contato = "".join(c for c in (row.get("contato") or "") if c.isdigit())
        if contato and (contato in digits or digits in contato):
            if (row.get("data_envio_site") or "").strip():
                return False
            return True
    return True


def lookup_segmento_pipeline(numero):
    """Acha o segmento do prospect (restaurante, clínica, etc.) — usado pra
    personalizar a resposta de preço."""
    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        return None
    digits = "".join(c for c in str(numero) if c.isdigit())
    for row in pipeline:
        contato = "".join(c for c in (row.get("contato") or "") if c.isdigit())
        if contato and (contato in digits or digits in contato):
            return (row.get("segmento") or "").strip() or None
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

    # 0a. Número já catalogado como bot → silêncio total, sem I/O.
    if _bot_conhecido(numero):
        log(f"[IGNORADO] bot conhecido — número={numero}", "INFO")
        return {"sent": False, "reason": "bot_conhecido", "lead_quente": False}

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

    # 1.5. BOT / cardápio digital / WhatsApp Business automático.
    #      Detectado por conteúdo da mensagem — ignora sem chamar Claude.
    bot_motivo = detectar_bot_whatsapp(texto_recebido)
    if bot_motivo:
        if bot_motivo in SOFT_BOT_MOTIVOS:
            # Padrão fraco (msg curta, só link, só emojis) — pula resposta
            # mas não marca o número como bot para sempre.
            log(f"[IGNORADO] fora de contexto ({bot_motivo}) — número={numero}", "INFO")
            _append_user_msg()
            save_conversa(numero, conversa)
            return {"sent": False, "reason": f"ignorado:{bot_motivo}",
                    "lead_quente": False}
        log(f"[IGNORADO] bot WhatsApp Business detectado: {bot_motivo} — número={numero}",
            "INFO")
        _registrar_bot(numero, bot_motivo)
        try:
            _marcar_bot_pipeline(numero, bot_motivo)
        except Exception as e:
            log(f"falhou marcar bot na pipeline: {e}", "ERROR")
        return {"sent": False, "reason": f"bot:{bot_motivo}", "lead_quente": False}

    # 1.6. LOOP: 3+ mensagens idênticas em LOOP_JANELA_SEG → bot.
    if _detectar_loop(conversa, texto_recebido):
        motivo = f"loop_{LOOP_MENSAGENS_IDENTICAS}x_{LOOP_JANELA_SEG}s"
        log(f"[IGNORADO] LOOP detectado ({motivo}) — número={numero}", "INFO")
        _registrar_bot(numero, motivo)
        try:
            _marcar_bot_pipeline(numero, motivo)
        except Exception as e:
            log(f"falhou marcar bot na pipeline (loop): {e}", "ERROR")
        _append_user_msg()
        save_conversa(numero, conversa)
        return {"sent": False, "reason": motivo, "lead_quente": False}

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

    # 3. Anti-spam: SÓ bloqueia se a MESMA mensagem do user chegou 2x em <5s.
    #    Cliente engajado que responde rápido com conteúdo NOVO não é spam —
    #    o Leo deve responder sempre que houver conteúdo diferente, mesmo em <5s.
    #    (regressão histórica: cliente respondia em 2s e o Leo travava por 15s,
    #    perdendo o lead.)
    ultimas_user = [m for m in conversa.get("mensagens", []) if m.get("role") == "user"]
    if ultimas_user:
        try:
            ultima = ultimas_user[-1]
            ts_ultima = datetime.fromisoformat(ultima["ts"])
            dentro_janela = (datetime.now() - ts_ultima).total_seconds() < ANTI_SPAM_SEGUNDOS
            mesma_msg = (
                (ultima.get("content") or "").strip().lower()
                == (texto_recebido or "").strip().lower()
            )
            if dentro_janela and mesma_msg:
                log(f"[{numero}] anti-spam: mesma msg em <{ANTI_SPAM_SEGUNDOS}s, skip", "INFO")
                _append_user_msg()
                save_conversa(numero, conversa)
                return {"sent": False, "reason": "anti_spam_msg_repetida",
                        "lead_quente": False}
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

    # 5.4. INBOUND COLD — primeiro contato sem prospecção prévia.
    #      Apresenta os 3 serviços e abre SPIN. Determinístico, zero token Claude.
    #      Próximas mensagens caem no Claude com a regra do SYSTEM_PROMPT.
    if _is_inbound_cold(numero, conversa):
        send_resp = send_whatsapp_via_evolution(numero, RESPOSTA_INBOUND_COLD)
        if send_resp.get("ok"):
            conversa["mensagens"].append({
                "role": "assistant", "content": RESPOSTA_INBOUND_COLD,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "dry_run": send_resp.get("dry_run", False),
            })
        save_conversa(numero, conversa)
        log(f"👋 [{numero}] inbound cold — boas-vindas com 3 serviços")
        return {"sent": send_resp.get("ok", False), "reason": "inbound_cold",
                "lead_quente": False, "resposta": RESPOSTA_INBOUND_COLD}

    # 5.5. PREÇO DIRETO — cliente manda só "Valores"/"Quanto custa" sem contexto.
    #      Resposta fixa com nome do segmento (vinda do pipeline) e abre SPIN.
    #      Só dispara uma vez por número (preco_handled). Próximas perguntas
    #      sobre preço caem no Claude com a regra do SYSTEM_PROMPT.
    if detectar_preco_direto(texto_recebido) and not conversa.get("preco_handled"):
        frase_seg = _frase_segmento(lookup_segmento_pipeline(numero))
        resposta_preco = (
            f"Depende do que você precisa! Me conta mais sobre {frase_seg} "
            f"que monto uma proposta personalizada pra você 😊"
        )
        send_resp = send_whatsapp_via_evolution(numero, resposta_preco)
        if send_resp.get("ok"):
            conversa["mensagens"].append({
                "role": "assistant", "content": resposta_preco,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "dry_run": send_resp.get("dry_run", False),
            })
        conversa["preco_handled"] = True
        save_conversa(numero, conversa)
        log(f"💰 [{numero}] preço direto — resposta padrão (seg={frase_seg})")
        return {"sent": send_resp.get("ok", False), "reason": "preco_direto",
                "lead_quente": False, "resposta": resposta_preco}

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

    # 8.5. Dedup por SIMILARIDADE (>= DEDUP_RESPOSTA_LIMIAR em
    #      DEDUP_RESPOSTA_JANELA_SEG). Pega despedidas e mensagens
    #      quase idênticas que escapariam de match exato.
    if _resposta_duplicada(conversa, resposta):
        log(f"[{numero}] resposta similar a uma de <{DEDUP_RESPOSTA_JANELA_SEG}s — skip", "INFO")
        save_conversa(numero, conversa)
        return {"sent": False, "reason": "resposta_duplicada",
                "lead_quente": False, "resposta": resposta}

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
