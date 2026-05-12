"""
Etapa 3 — gera mensagens personalizadas por prospect.
Saída: ~/scout/mensagens/[empresa].txt com WhatsApp + Email.

Se ANTHROPIC_API_KEY estiver configurada, usa Claude API (mais personalização).
Caso contrário, cai num template paramétrico de qualidade.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    MENS_DIR, QUALIFICADOS_CSV, env, load_env, log, read_csv, slugify,
)


SISTEMA_PROMPT = """Você é o assistente comercial da Scout Company, que vende 3 serviços:
  1. SITES profissionais (entrega 7 dias) — pra negócio aparecer no Google
  2. SISTEMAS de gestão sob medida — pra operação parar de usar papel/planilha
  3. AUTOMAÇÃO COM IA (prospecção/atendimento/conteúdo) — pra empresa crescer sem contratar

Você está escrevendo mensagem fria via WhatsApp e e-mail. Vai te ser dado o SERVIÇO certo
pra esse prospect — escreva focado SÓ nesse serviço, sem misturar os outros.

Sua escrita:
- Tom: humano, direto, consultivo. NÃO comercial-batido. Voz da Scout (empresa).
- Português brasileiro, casual mas profissional. Sem clichês ("estamos no mercado há X anos").
- Nunca prometa o que não foi pedido. Nunca invente dados.
- Não cite benefícios genéricos ("aumentar vendas"). Aborde o problema ESPECÍFICO do negócio.
- Use SOMENTE os dados fornecidos."""

# Bandeiras-base (referência de estilo/conteúdo) — Claude personaliza a partir delas
BANDEIRA_SITE = """Oi! Vi que [NOME] tem [X] avaliações no Google — resultado de quem faz um trabalho sério.
Só que quem busca [SEGMENTO] online em [CIDADE] e não encontra um site acaba indo pro concorrente.
A Scout cria sites profissionais pra [SEGMENTO] — simples, bonitos e fáceis de encontrar.
Dá uma olhada: scoutcompany.com.br — se fizer sentido, me fala por aqui."""

BANDEIRA_SISTEMA = """Oi! Vi que [NOME] tem uma operação consolidada em [CIDADE] — as avaliações mostram isso.
Muitas empresas do seu segmento ainda gerenciam tudo no papel ou em planilhas, perdendo tempo e dinheiro.
A Scout desenvolve sistemas sob medida pra [SEGMENTO] — controle de clientes, agendamentos, financeiro, tudo num lugar só.
Dá uma olhada: scoutcompany.com.br — se fizer sentido, me conta mais sobre sua operação."""

BANDEIRA_AUTOMACAO = """Oi! Vi que [NOME] atua em [SEGMENTO] — mercado onde quem aparece mais, vende mais.
A Scout implementa automação com IA pra empresas como a sua: prospecção automática, atendimento 24h e geração de conteúdo no piloto automático.
Dá uma olhada: scoutcompany.com.br — se fizer sentido, me conta mais sobre sua operação."""

BANDEIRAS = {
    "site": BANDEIRA_SITE,
    "sistema": BANDEIRA_SISTEMA,
    "automacao": BANDEIRA_AUTOMACAO,
}

SERVICO_DESCRICAO = {
    "site": "site profissional (entrega 7 dias, otimizado pra Google, responsivo)",
    "sistema": "sistema de gestão sob medida (clientes/agendamentos/financeiro num só lugar)",
    "automacao": "automação com IA (prospecção, atendimento 24h via WhatsApp, geração de conteúdo)",
}

USER_PROMPT_TEMPLATE = """Gere a mensagem de WhatsApp e o email para este prospect. UMA versão polida de cada — não duas, não alternativas.

SERVIÇO QUE A SCOUT VAI OFERECER (foco ÚNICO da mensagem):
{servico_descricao}

REFERÊNCIA DE TOM/ESTRUTURA (não copie literal, use como base de inspiração):
{bandeira}

DADOS DO NEGÓCIO:
- Nome: {nome}
- Segmento: {segmento}
- Cidade: {cidade}
- Endereço: {endereco}
- Avaliação Google: {rating} ({n_reviews} avaliações)
- Telefone: {telefone}
- Instagram: {instagram}
- Site: {site_str}
- Situação digital identificada: {situacao}

REFERÊNCIA GEOGRÁFICA (OBRIGATÓRIA):
- Mencione naturalmente a cidade ({cidade}) na mensagem — uma vez no WhatsApp, uma vez no email.
- Exemplos naturais: "aqui em {cidade}", "no mercado de {cidade}", "quem busca [segmento] em {cidade}".
- NÃO escreva "na sua região" ou "aqui na região" — use o nome da cidade.

FOCO DO SERVIÇO (CRÍTICO):
- Fale APENAS do serviço acima ({servico_label}). NÃO misture os outros 2 serviços nessa mensagem.
- Aborde o problema concreto que esse serviço resolve pra esse tipo de negócio.

ASSINATURA (use no email, NÃO no WhatsApp):
{assinatura_nome} | {assinatura_telefone}

REGRAS DA MENSAGEM DE WHATSAPP:
- Curta. Máximo 90 palavras. Pessoa lê no celular, em pé, distraída.
- 4 parágrafos curtos separados por linha em branco.
- Parágrafo 1: 1-2 frases. Cumprimento + elogio específico (use as avaliações reais).
- Parágrafo 2: 1-2 frases. Diagnóstico do problema digital (sem site / site antigo / só Instagram). Linguagem comum, sem jargão.
- Parágrafo 3: 1 frase. Como a Scout resolve isso. Tom de empresa, não de pessoa.
- Parágrafo 4 (CTA OBRIGATÓRIO com LINK): convite pra conhecer o site + opção de continuar conversa. Use UMA destas estruturas (não invente outras):
  • "Dá uma olhada no que fazemos: scoutcompany.com.br — ou responde aqui se quiser conversar."
  • "Tem mais exemplos em scoutcompany.com.br. Se quiser, é só me responder por aqui."
  • "scoutcompany.com.br tem nossos projetos — se fizer sentido, dá pra conversar por aqui mesmo."
- NÃO incluir assinatura nominal no WhatsApp (a Scout é a marca, não pessoa).
- O link scoutcompany.com.br DEVE aparecer literalmente — não use "nosso site" sem o link.
- Tom: humano, direto, sem soar comercial. Voz da Scout (empresa), não 1ª pessoa.

REGRAS DO EMAIL:
- Mais formal que o WhatsApp, mas direto.
- 4 parágrafos curtos. Estrutura: elogio → diagnóstico → proposta → CTA.
- O CTA do email DEVE conter o link scoutcompany.com.br + alternativa de contato.
  Algo natural tipo: "Pra conhecer nossos projetos, acesse scoutcompany.com.br.
  Se preferir conversar por WhatsApp, é só me chamar: {assinatura_telefone}."
  (adapte ao tom do email — mas o link DEVE aparecer)
- Terminar com:
Atenciosamente,
Equipe Scout
🌐 scoutcompany.com.br
📱 WhatsApp: {assinatura_telefone}

Devolva no formato exato (sem "Versão 1", sem "Versão 2", sem alternativas):

===WHATSAPP===
[mensagem única de WhatsApp seguindo as regras acima]

===EMAIL_ASSUNTO===
[Assunto conciso, máx 60 caracteres. Ex: "Site profissional para {nome}"]

===EMAIL_CORPO===
[corpo do email seguindo as regras acima]"""


def gerar_via_claude(prospect, assinatura_nome, assinatura_telefone):
    """Usa Anthropic API. Retorna (whatsapp, email_assunto, email_corpo) ou None se falhar."""
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError:
        log("anthropic SDK não instalado", "WARN")
        return None

    client = Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    model = env("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    site = (prospect.get("site") or "").strip()
    site_str = site if site else "(sem site)"
    insta = (prospect.get("instagram") or "").strip() or "(sem Instagram)"

    cidade = (prospect.get("cidade") or "").strip()
    if not cidade or len(cidade) <= 2:  # "SP" sem cidade real → fallback gentil
        cidade = "sua cidade"

    servico = (prospect.get("servico_recomendado") or "site").strip().lower()
    if servico not in BANDEIRAS:
        servico = "site"

    user_prompt = USER_PROMPT_TEMPLATE.format(
        servico_label=servico.upper(),
        servico_descricao=SERVICO_DESCRICAO[servico],
        bandeira=BANDEIRAS[servico],
        nome=prospect.get("nome", ""),
        segmento=prospect.get("segmento", ""),
        cidade=cidade,
        endereco=prospect.get("endereco", ""),
        rating=prospect.get("rating", "0"),
        n_reviews=prospect.get("user_ratings_total", "0"),
        telefone=prospect.get("telefone", "") or "(sem telefone)",
        instagram=insta,
        site_str=site_str,
        situacao=prospect.get("situacao", ""),
        assinatura_nome=assinatura_nome,
        assinatura_telefone=assinatura_telefone,
    )

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=SISTEMA_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = resp.content[0].text
        return parse_claude_response(text)
    except Exception as e:
        log(f"Falha na chamada Claude API: {e}", "WARN")
        return None


def parse_claude_response(text):
    """Faz parse do formato ===WHATSAPP=== / ===EMAIL_ASSUNTO=== / ===EMAIL_CORPO===."""
    parts = {"whatsapp": "", "email_assunto": "", "email_corpo": ""}
    current = None
    buffer = []
    for line in text.splitlines():
        marker = line.strip()
        if marker == "===WHATSAPP===":
            if current and buffer:
                parts[current] = "\n".join(buffer).strip()
            current = "whatsapp"
            buffer = []
        elif marker == "===EMAIL_ASSUNTO===":
            if current and buffer:
                parts[current] = "\n".join(buffer).strip()
            current = "email_assunto"
            buffer = []
        elif marker == "===EMAIL_CORPO===":
            if current and buffer:
                parts[current] = "\n".join(buffer).strip()
            current = "email_corpo"
            buffer = []
        else:
            if current:
                buffer.append(line)
    if current and buffer:
        parts[current] = "\n".join(buffer).strip()
    if not parts["whatsapp"] or not parts["email_corpo"]:
        return None
    return parts["whatsapp"], parts["email_assunto"], parts["email_corpo"]


# ----------------------------
# FALLBACK — template paramétrico
# ----------------------------
_MASC_PREFIXOS = (
    "restaurante", "buffet", "espaço", "espaco", "studio", "estúdio",
    "centro", "hospital", "escritório", "escritorio", "salão", "salao",
    "auto ", "sushi", "sr ", "sr.", "supermercado", "atelie", "ateliê",
    "instituto", "consultório", "consultorio",
)


def _artigo_definido(nome):
    """Retorna 'a' ou 'o' baseado no primeiro substantivo do nome."""
    if not nome:
        return "a"
    n = nome.lower().strip()
    for pref in _MASC_PREFIXOS:
        if n.startswith(pref):
            return "o"
    return "a"


def gerar_via_template(prospect, assinatura_nome, assinatura_telefone):
    nome = prospect.get("nome", "").strip()
    artigo = _artigo_definido(nome)
    segmento = (prospect.get("segmento") or "").strip()
    rating = prospect.get("rating", "0")
    n_reviews = prospect.get("user_ratings_total", 0)
    site = (prospect.get("site") or "").strip()
    insta = (prospect.get("instagram") or "").strip()
    situacao = (prospect.get("situacao") or "").lower()
    cidade = (prospect.get("cidade") or "").strip()
    referencia_local = f"em {cidade}" if cidade and len(cidade) > 2 else "na sua região"
    servico = (prospect.get("servico_recomendado") or "site").strip().lower()

    # Elogio baseado em rating + reviews
    try:
        rt = float(rating)
        nr = int(float(n_reviews))
    except (ValueError, TypeError):
        rt = 0.0
        nr = 0

    if rt >= 4.7 and nr >= 100:
        elogio = (f"Vi {artigo} {nome} no Google e fiquei impressionado — "
                  f"{rt:.1f} de avaliação com mais de {nr} reviews diz muita coisa. "
                  f"Difícil manter esse nível em {segmento.lower()}.")
    elif rt >= 4.5 and nr >= 50:
        elogio = (f"Vi {artigo} {nome} no Google ({rt:.1f} de avaliação, {nr} reviews) — "
                  f"dá pra ver que vocês cuidam bem do que entregam.")
    elif rt >= 4.5:
        elogio = (f"Cheguei n{artigo} {nome} pesquisando {segmento.lower()} {referencia_local} — "
                  f"a avaliação de {rt:.1f} mostra que o cliente sai satisfeito.")
    else:
        elogio = (f"Estava mapeando negócios de {segmento.lower()} {referencia_local} "
                  f"e {artigo} {nome} apareceu nas referências.")

    # Diagnóstico + CTA dependem do SERVIÇO recomendado pro prospect
    if servico == "automacao":
        diagnostico = (
            f"Em {segmento.lower()}, quem aparece mais vende mais — e prospectar manualmente "
            "consome o tempo de quem deveria estar fechando contrato. A Scout implementa "
            "automação com IA: prospecção contínua, atendimento 24h e geração de conteúdo "
            "no piloto automático."
        )
        cta = ("Dá uma olhada nos nossos projetos: scoutcompany.com.br. "
               "Se fizer sentido, me conta um pouco da sua operação que monto algo sob medida.")
    elif servico == "sistema":
        diagnostico = (
            f"Muitas empresas de {segmento.lower()} ainda controlam clientes, agendamentos "
            "e financeiro em planilhas ou no caderno — perdendo tempo e dinheiro quando o "
            "volume cresce. A Scout desenvolve sistemas sob medida pra esse tipo de operação, "
            "centralizando tudo num lugar só."
        )
        cta = ("Dá uma olhada: scoutcompany.com.br. Se fizer sentido, "
               "me conta mais sobre como vocês trabalham hoje.")
    else:
        # site — variações específicas por situação digital
        if "sem site" in situacao or (not site and not insta):
            diagnostico = ("Reparei que vocês não têm site. Hoje, quando alguém busca "
                           f"no Google por {segmento.lower()} {referencia_local}, quem aparece é a "
                           "concorrência — mesmo quando o serviço de vocês é melhor. "
                           "Sem uma página onde o cliente novo encontre informação "
                           "rápida e um caminho pro contato, ele vai pro próximo "
                           "resultado.")
            cta = ("Tenho um exemplo de site pronto que dá pra adaptar pra vocês "
                   "em 1-2 dias. Posso te mandar o preview agora, sem compromisso?")
        elif "só instagram" in situacao or (not site and insta):
            diagnostico = ("Vi que a presença digital de vocês está concentrada no "
                           f"Instagram ({insta}). É um bom canal, mas Instagram não "
                           "aparece no Google quando alguém pesquisa pelo serviço — "
                           "e cliente novo pesquisa antes de comprar. Sem site, vocês "
                           "estão deixando de ser encontrados.")
            cta = ("Posso te mostrar em 5 minutos como ficaria um site simples "
                   f"pra {segmento.lower()}, com WhatsApp integrado e link direto pro Insta?")
        elif "site desatualizado" in situacao or "site antigo" in situacao or "site fraco" in situacao:
            diagnostico = ("Dei uma olhada no site de vocês e ele tem alguns pontos "
                           "que dão pra modernizar — principalmente como aparece no "
                           "celular (que é onde 80% dos clientes navegam hoje). "
                           "Site antigo passa a impressão de negócio antigo, mesmo "
                           "quando o serviço é de ponta.")
            cta = ("Tenho um exemplo de redesign que posso adaptar com a identidade "
                   "de vocês. Posso te enviar o preview?")
        else:
            diagnostico = ("Olhando a presença digital de vocês, identifiquei "
                           "alguns pontos onde dá pra melhorar a captação de "
                           "clientes — sem precisar gastar com tráfego pago.")
            cta = ("Posso te mostrar em 5 minutos o que é possível? Sem compromisso.")

    # WhatsApp version (4 parágrafos curtos, tom de empresa, com link obrigatório)
    whatsapp = f"""Oi! Tudo bem?

{elogio}

{diagnostico}

{cta}

Dá uma olhada no que fazemos: scoutcompany.com.br — ou responde aqui se quiser conversar."""

    # Email version (assinado pela Equipe Scout, com link)
    email_assunto = f"Site profissional para {nome}"
    email_corpo = f"""Olá!

{elogio}

{diagnostico}

A Scout entrega site profissional sob medida, responsivo, com WhatsApp integrado e otimizado pro Google. Entrega em até 7 dias, sem mensalidade de plataforma.

{cta}

Pra conhecer nossos projetos, acesse scoutcompany.com.br. Se preferir conversar por WhatsApp, é só me chamar: {assinatura_telefone}.

Atenciosamente,
Equipe Scout
🌐 scoutcompany.com.br
📱 WhatsApp: {assinatura_telefone}
"""
    return whatsapp.strip(), email_assunto, email_corpo.strip()


def salvar_mensagem(prospect, whatsapp, email_assunto, email_corpo):
    MENS_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(prospect.get("nome", "") or prospect.get("id", "sem-nome"))
    path = MENS_DIR / f"{slug}.txt"
    content = f"""# {prospect.get('nome','')} — {prospect.get('segmento','')}
# Serviço recomendado: {(prospect.get('servico_recomendado') or 'site').upper()}
# Contato: {prospect.get('telefone','')} | {prospect.get('instagram','')}
# Endereço: {prospect.get('endereco','')}
# Score: {prospect.get('score','')}/10 — {prospect.get('situacao','')}

═══════════════════════════════════════════
WHATSAPP
═══════════════════════════════════════════

{whatsapp}

═══════════════════════════════════════════
EMAIL — Assunto:
═══════════════════════════════════════════
{email_assunto}

═══════════════════════════════════════════
EMAIL — Corpo:
═══════════════════════════════════════════

{email_corpo}
"""
    path.write_text(content, encoding="utf-8")
    return path


def main():
    load_env()
    qualificados = read_csv(QUALIFICADOS_CSV)
    if not qualificados:
        log("Nenhum prospect qualificado. Rode qualify.py primeiro.", "WARN")
        return

    has_anthropic = bool(env("ANTHROPIC_API_KEY"))
    log(f"Gerando mensagens pra {len(qualificados)} prospects "
        f"(modo: {'CLAUDE_API' if has_anthropic else 'TEMPLATE'})")

    assinatura_nome = env("ASSINATURA_NOME", "Augusto Barbosa")
    assinatura_tel = env("ASSINATURA_TELEFONE", "")

    paths_gerados = []
    for p in qualificados:
        result = None
        if has_anthropic:
            result = gerar_via_claude(p, assinatura_nome, assinatura_tel)
        if not result:
            result = gerar_via_template(p, assinatura_nome, assinatura_tel)

        whatsapp, email_assunto, email_corpo = result
        path = salvar_mensagem(p, whatsapp, email_assunto, email_corpo)
        paths_gerados.append(path)
        log(f"  ✅ {path.name}")

    log(f"Total mensagens geradas: {len(paths_gerados)}")
    print(f"MENSAGENS_GERADAS={len(paths_gerados)}")
    return paths_gerados


if __name__ == "__main__":
    main()
