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


SISTEMA_PROMPT = """Você é o Leo, assistente da Scout Company. Está escrevendo uma mensagem de WhatsApp pra um dono de pequeno negócio.

A Scout oferece 3 serviços (UMA por mensagem, nunca mistura os outros):
  1. SITES profissionais (entrega rápida, pra aparecer no Google e converter)
  2. SISTEMAS de gestão sob medida (substituir caderno e planilha)
  3. AUTOMAÇÃO COM IA (prospecção, atendimento, conteúdo)

Tom: profissional mas acessível. Direto ao ponto, explicativo quando for necessário.
Português brasileiro. Frases curtas e naturais, igual gente escreve no WhatsApp,
mas sem cair em gírias forçadas. Nem corporativo, nem informal demais.

PROIBIDO ABSOLUTO (a mensagem é descartada se aparecer):
- Asterisco pra negrito: nada de *texto*. Tudo texto simples.
- Underline: nada de _texto_.
- Tachado: nada de ~texto~.
- Nome da empresa em destaque: escreve "Scout", nunca "*Scout*".
- Travessão (—) como separador. Use ponto, vírgula ou quebra de linha.
- Bullet points, listas, marcadores tipo "•", "-" ou numeração.
- Palavras excessivamente corporativas: "solução", "entregar valor", "potencializar",
  "alavancar", "no piloto automático", "agregar valor", "robusto", "ecossistema",
  "performance", "engajamento", "consolidada".
- Clichês de vendedor: "estamos no mercado há X anos", "líderes em", "referência em".

OBRIGATÓRIO:
- Abre com "Oi, aqui é o Leo da Scout!" (exato, na primeira linha).
- No máximo 4 parágrafos curtos. Pode ter parágrafo de uma frase só.
- Quando explicar o problema/serviço, dá um exemplo concreto do segmento do cliente.
- Termina com uma pergunta simples e natural.
- Usa só os dados fornecidos. Nunca inventa número."""

# Exemplos de tom (NÃO copiar literal, só inspirar o jeito de escrever)
BANDEIRA_SITE = """Oi, aqui é o Leo da Scout!

Vi o [NOME] no Google Maps com ótimas avaliações em [SEGMENTO].

Reparei que vocês ainda não têm site. Isso faz diferença porque muita gente pesquisa online antes de visitar ou contratar, e quem não aparece no Google acaba perdendo esse cliente pra concorrência.

Posso mostrar como ficaria para o seu negócio? Tem exemplos em scoutcompany.com.br"""

BANDEIRA_SISTEMA = """Oi, aqui é o Leo da Scout!

Vi o [NOME] aqui em [CIDADE], uma operação bem cuidada de [SEGMENTO].

Pergunta rápida: como vocês organizam cliente, agenda e financeiro hoje? Muita empresa do seu porte ainda controla tudo em caderno ou planilha, e isso pesa quando o movimento cresce. A Scout desenvolve sistemas sob medida pra esse tipo de operação, com tudo centralizado num lugar só.

Quer ver alguns exemplos? scoutcompany.com.br"""

BANDEIRA_AUTOMACAO = """Oi, aqui é o Leo da Scout!

Vi o [NOME] no Google, trabalho consistente em [SEGMENTO].

Você já considerou automatizar a parte mais repetitiva do dia a dia? Prospecção de cliente novo, atendimento de WhatsApp fora do horário, criação de conteúdo recorrente. A Scout monta esse tipo de automação com IA, ajustada pro fluxo da sua empresa.

Posso te mostrar como funciona? scoutcompany.com.br"""

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

USER_PROMPT_TEMPLATE = """Gera uma mensagem de WhatsApp e um email pra esse prospect. UMA versão de cada, sem alternativa.

SERVIÇO PRA OFERECER (só esse, não mistura os outros):
{servico_descricao}

EXEMPLO DE TOM (NÃO copia literal, só inspira o jeito de escrever):
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
- Situação digital: {situacao}

REGRAS DA MENSAGEM DE WHATSAPP (segue à risca):

- Abre EXATAMENTE com "Oi, aqui é o Leo da Scout!" na primeira linha.
- No máximo 4 parágrafos curtos, separados por linha em branco.
- Pode ter parágrafo de uma frase. Mistura tamanhos.
- Tom profissional mas acessível. Nem corporativo, nem gíria.
- ZERO formatação WhatsApp: nada de *negrito*, _itálico_ ou ~tachado~. Texto simples.
- ZERO travessão (—). Use ponto, vírgula ou quebra de linha.
- ZERO bullet, lista ou marcador.
- ZERO palavra corporativa (ver system prompt).
- Nome da empresa em texto simples: "Scout", não "*Scout*".

ESTRUTURA RECOMENDADA:
- Parágrafo 1: "Oi, aqui é o Leo da Scout!".
- Parágrafo 2: cita {nome} + avaliação real ({rating} estrelas{n_reviews_str}) com naturalidade.
- Parágrafo 3: aponta a oportunidade ligada ao serviço {servico_label} e explica em uma frase
  porque isso importa pra um negócio de {segmento}. Usa exemplo concreto se ajudar.
- Parágrafo 4: proposta + pergunta simples + link scoutcompany.com.br.
  Se a mensagem ficar boa em 3 parágrafos, não força um 4º.

CIDADE:
- Menciona {cidade} uma vez, natural. Sem "na sua região".

FECHAMENTOS QUE FUNCIONAM (escolhe um):
- "Posso mostrar como ficaria para o seu negócio? Tem exemplos em scoutcompany.com.br"
- "Quer ver alguns exemplos? scoutcompany.com.br"
- "Posso te mostrar como funciona? scoutcompany.com.br"
- "Te mando um preview? Os projetos estão em scoutcompany.com.br"

NÃO escreve assinatura no final do WhatsApp. O Leo já apareceu na abertura.

ASSINATURA PRO EMAIL:
{assinatura_nome} | {assinatura_telefone}

REGRAS DO EMAIL:
- Pode ser um pouco mais formal que o WhatsApp. Sem palavra corporativa.
- 3 a 4 parágrafos curtos. Sem travessão. Sem bullet. Sem asterisco/underline/tachado.
- Abertura: "Olá!" (sem repetir "Oi, aqui é o Leo da Scout!").
- O Leo é quem assina: usa "eu" naturalmente, voz pessoal mas profissional.
- CTA contém scoutcompany.com.br + opção de WhatsApp ({assinatura_telefone}).
- Termina exatamente assim:

Atenciosamente,
Leo / Scout
🌐 scoutcompany.com.br
📱 WhatsApp: {assinatura_telefone}

Devolve no formato exato (sem "Versão 1", sem alternativa):

===WHATSAPP===
[mensagem única de WhatsApp]

===EMAIL_ASSUNTO===
[assunto conciso, máx 60 caracteres. Ex: "Site profissional para {nome}"]

===EMAIL_CORPO===
[corpo do email]"""


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

    n_reviews_raw = prospect.get("user_ratings_total", "0")
    try:
        n_reviews_int = int(float(n_reviews_raw))
    except (ValueError, TypeError):
        n_reviews_int = 0
    n_reviews_str = f" com {n_reviews_int} avaliações" if n_reviews_int >= 5 else ""

    user_prompt = USER_PROMPT_TEMPLATE.format(
        servico_label=servico.upper(),
        servico_descricao=SERVICO_DESCRICAO[servico],
        bandeira=BANDEIRAS[servico],
        nome=prospect.get("nome", ""),
        segmento=prospect.get("segmento", ""),
        cidade=cidade,
        endereco=prospect.get("endereco", ""),
        rating=prospect.get("rating", "0"),
        n_reviews=n_reviews_raw,
        n_reviews_str=n_reviews_str,
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
    segmento = (prospect.get("segmento") or "").strip().lower()
    rating = prospect.get("rating", "0")
    n_reviews = prospect.get("user_ratings_total", 0)
    site = (prospect.get("site") or "").strip()
    insta = (prospect.get("instagram") or "").strip()
    situacao = (prospect.get("situacao") or "").lower()
    cidade = (prospect.get("cidade") or "").strip()
    cidade_ok = cidade if cidade and len(cidade) > 2 else ""
    em_cidade = f" em {cidade_ok}" if cidade_ok else ""
    servico = (prospect.get("servico_recomendado") or "site").strip().lower()

    try:
        rt = float(rating)
        nr = int(float(n_reviews))
    except (ValueError, TypeError):
        rt = 0.0
        nr = 0

    saudacao = "Oi, aqui é o Leo da Scout!"

    # Linha de elogio baseada na avaliação real (Google Maps)
    if rt >= 4.7 and nr >= 50:
        elogio = (f"Vi {artigo} {nome} no Google Maps com {rt:.1f} estrelas em {nr} avaliações. "
                  f"Reputação consistente em {segmento}.")
    elif rt >= 4.5 and nr >= 15:
        elogio = (f"Vi {artigo} {nome} no Google Maps com {rt:.1f} estrelas e {nr} avaliações. "
                  f"Trabalho bem reconhecido em {segmento}.")
    elif nr > 0:
        elogio = f"Encontrei {artigo} {nome} pesquisando {segmento}{em_cidade}."
    else:
        elogio = f"Cheguei até {artigo} {nome} pesquisando {segmento}{em_cidade}."

    # Miolo + fechamento variam por serviço
    if servico == "automacao":
        miolo = ("Você já considerou automatizar a parte mais repetitiva do dia a dia? "
                 "Prospecção de cliente novo, atendimento de WhatsApp fora do horário e "
                 "criação de conteúdo recorrente. A Scout monta esse tipo de automação "
                 "com IA, ajustada pro fluxo da sua empresa.")
        fechamento = "Posso te mostrar como funciona? scoutcompany.com.br"
    elif servico == "sistema":
        miolo = (f"Muita empresa de {segmento} ainda organiza cliente, agenda e financeiro "
                 "em caderno ou planilha, e isso pesa quando o movimento cresce. A Scout "
                 "desenvolve sistemas sob medida pra esse tipo de operação, com tudo "
                 "centralizado num lugar só.")
        fechamento = "Quer ver alguns exemplos? scoutcompany.com.br"
    else:
        if "sem site" in situacao or (not site and not insta):
            miolo = ("Reparei que vocês ainda não têm site. Isso faz diferença porque "
                     "muita gente pesquisa online antes de visitar ou contratar, e quem "
                     "não aparece no Google acaba perdendo esse cliente pra concorrência.")
            fechamento = "Posso mostrar como ficaria para o seu negócio? Tem exemplos em scoutcompany.com.br"
        elif "só instagram" in situacao or (not site and insta):
            miolo = ("Vi que a presença de vocês está concentrada no Instagram. É um canal "
                     "importante, mas ele não aparece no Google quando alguém pesquisa pelo "
                     "serviço, e a maioria dos clientes novos passa por ali antes de fechar.")
            fechamento = "Posso te mostrar como um site simples resolveria isso? scoutcompany.com.br"
        elif "site desatualizado" in situacao or "site antigo" in situacao or "site fraco" in situacao:
            miolo = ("Dei uma olhada no site de vocês e tem pontos que dá pra modernizar, "
                     "principalmente como ele aparece no celular. Site antigo passa a impressão "
                     "de negócio antigo, mesmo quando o serviço é de ponta.")
            fechamento = "Te mando um preview de como ficaria? Os projetos estão em scoutcompany.com.br"
        else:
            miolo = ("Olhando a presença digital de vocês, vi alguns pontos onde a Scout "
                     "consegue ajudar a melhorar a captação de clientes.")
            fechamento = "Posso te mostrar em poucos minutos? scoutcompany.com.br"

    whatsapp = f"""{saudacao}

{elogio}

{miolo}

{fechamento}"""

    # Email
    if servico == "automacao":
        email_assunto = f"Automação com IA para {nome}"
        problema_email = ("Muita coisa do dia a dia se repete: prospecção, primeiro "
                          "atendimento no WhatsApp e criação de conteúdo. A Scout monta "
                          "automação com IA pra resolver essa parte sem precisar contratar "
                          "mais gente.")
    elif servico == "sistema":
        email_assunto = f"Sistema de gestão para {nome}"
        problema_email = (f"Muita empresa de {segmento} ainda controla cliente, agenda e "
                          "financeiro em caderno ou planilha, e isso vira gargalo quando o "
                          "volume cresce. A Scout desenvolve sistemas sob medida pra esse "
                          "tipo de operação.")
    else:
        if "sem site" in situacao or (not site and not insta):
            problema_email = (f"Reparei que vocês ainda não têm site. Quando alguém "
                              f"pesquisa {segmento}{em_cidade}, quem aparece é a "
                              "concorrência, mesmo quando o serviço de vocês é melhor.")
        elif "só instagram" in situacao or (not site and insta):
            problema_email = (f"Vi que a presença de vocês está concentrada no Instagram "
                              f"({insta}). É um canal importante, mas não aparece no Google. "
                              "A maior parte dos clientes novos pesquisa antes de chegar.")
        else:
            problema_email = ("Dei uma olhada na presença digital de vocês e identifiquei "
                              "pontos onde a Scout consegue ajudar.")
        email_assunto = f"Site profissional para {nome}"

    email_corpo = f"""Olá!

Aqui é o Leo, da Scout. {elogio}

{problema_email}

Para conhecer alguns projetos, acessa scoutcompany.com.br. Se preferir conversar por WhatsApp, é só me chamar: {assinatura_telefone}.

Atenciosamente,
Leo / Scout
🌐 scoutcompany.com.br
📱 WhatsApp: {assinatura_telefone}
"""
    return whatsapp.strip(), email_assunto, email_corpo.strip()


def salvar_mensagem(prospect, whatsapp, email_assunto, email_corpo):
    MENS_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(prospect.get("nome", "") or prospect.get("id", "sem-nome"))
    path = MENS_DIR / f"{slug}.txt"
    content = f"""# {prospect.get('nome','')} / {prospect.get('segmento','')}
# Serviço recomendado: {(prospect.get('servico_recomendado') or 'site').upper()}
# Contato: {prospect.get('telefone','')} | {prospect.get('instagram','')}
# Endereço: {prospect.get('endereco','')}
# Score: {prospect.get('score','')}/10 / {prospect.get('situacao','')}

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
