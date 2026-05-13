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


SISTEMA_PROMPT = """Você está digitando uma mensagem de WhatsApp pra um dono de pequeno negócio, em nome da Scout Company.

A Scout vende 3 coisas (UMA por mensagem, nunca mistura):
  1. SITES (entrega rápida, pra aparecer no Google)
  2. SISTEMAS de gestão sob medida (acabar com caderno e planilha)
  3. AUTOMAÇÃO COM IA (prospecção, atendimento, conteúdo)

Como você escreve. LEIA COM ATENÇÃO:

Você está digitando no celular, igual gente faz no WhatsApp. Sem firula, sem cara de robô.
Frase curta. Pode ter uma palavra ou duas soltas numa linha. Pode ter parágrafo de uma frase só.
Português falado do Brasil. Tipo conversa mesmo, não argumentação de vendedor.

PROIBIDO ABSOLUTO (a mensagem é descartada se aparecer):
- Travessão (—) ou meio-traço como separador. Use ponto, vírgula ou quebra de linha.
- Bullet points, listas, marcadores.
- Frases simétricas e estruturadas demais (tipo "A, B e C" tudo perfeitinho).
- Parágrafos do mesmo tamanho, paralelos.
- Palavras corporativas: "solução", "entregar valor", "potencializar", "alavancar",
  "responsivo", "no piloto automático", "otimizar", "agregar valor", "estratégico",
  "diferencial", "performance", "engajamento", "consolidada", "robusto", "ecossistema".
- Clichês de vendedor: "estamos no mercado há X anos", "líderes em", "referência em".

OBRIGATÓRIO:
- Soa como mensagem real de alguém que entende do ramo do cara.
- Frases de tamanhos bem diferentes. Mistura.
- Termina com uma pergunta simples e natural.
- Nunca inventa dado. Usa só o que foi passado."""

# Exemplos de tom (NÃO copiar literal — só inspirar o jeito de escrever)
BANDEIRA_SITE = """Oi! Vi o [NOME] aqui no Google, [X] estrelas. Trabalho bem feito.

Reparei que vocês não têm site ainda.
Sei que parece detalhe, mas muita gente pesquisa [SEGMENTO] em [CIDADE] antes de ir.

Posso mostrar como ficaria? Tem uns projetos em scoutcompany.com.br."""

BANDEIRA_SISTEMA = """Oi! Tava olhando o [NOME] aqui em [CIDADE], boa pegada de [SEGMENTO].

Pergunta rápida: como vocês controlam cliente e agenda hoje? Caderno, planilha?
A gente faz sistema sob medida pra esse tipo de operação. Tudo num lugar só.

Dá uma olhada em scoutcompany.com.br. Se fizer sentido, me chama aqui."""

BANDEIRA_AUTOMACAO = """Oi! Vi o [NOME] no Google. Operação bonita de [SEGMENTO].

Você já pensou em automatizar a parte chata?
Prospectar cliente novo. Responder WhatsApp 24h. Gerar post sem você pensar.

A gente faz isso com IA. Quer ver como ficaria? scoutcompany.com.br tem alguns exemplos."""

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

- No máximo 3 parágrafos. Curtos.
- Frases curtas. Igual gente digita no celular.
- Pode quebrar linha no meio de um parágrafo se quiser dar respiro.
- Pode ter parágrafo de uma frase só.
- ZERO travessão (—). ZERO bullet ou lista. ZERO palavra corporativa.
- ZERO frase simétrica perfeita.

ESTRUTURA NATURAL (não rotula, só usa de guia mental):
- Abre com "Oi!" e cita o nome do negócio + a avaliação real ({rating} estrelas{n_reviews_str}). Sem exagero.
- Aponta o problema do jeito casual. Tipo "Reparei que...", "Vi que...", "Sei que parece detalhe...".
- Faz uma proposta simples. Termina com uma pergunta direta e natural.
- O link scoutcompany.com.br aparece literal (na proposta ou logo depois da pergunta).

CIDADE:
- Menciona {cidade} uma vez, natural. Sem "na sua região".

FECHAMENTOS QUE FUNCIONAM (varia o estilo, escolhe um jeito):
- "Posso mostrar como ficaria? Tem uns exemplos em scoutcompany.com.br."
- "Quer ver como ficaria? scoutcompany.com.br tem uns projetos lá."
- "Dá uma olhada em scoutcompany.com.br. Se fizer sentido, me responde aqui."
- "Te mando um preview? scoutcompany.com.br tem o estilo da gente."

NÃO escreve assinatura nominal no WhatsApp. Só o nome Scout aparece (pela marca).

ASSINATURA PRO EMAIL:
{assinatura_nome} | {assinatura_telefone}

REGRAS DO EMAIL:
- Pode ser um pouco mais formal que o WhatsApp, mas sem palavra corporativa.
- 3 parágrafos curtos. Sem travessão. Sem bullet.
- CTA do email contém scoutcompany.com.br + opção de WhatsApp ({assinatura_telefone}).
- Termina exatamente assim:

Atenciosamente,
Equipe Scout
🌐 scoutcompany.com.br
📱 WhatsApp: {assinatura_telefone}

Devolve no formato exato (sem "Versão 1", sem alternativa):

===WHATSAPP===
[mensagem única de WhatsApp]

===EMAIL_ASSUNTO===
[assunto conciso, máx 60 caracteres. Ex: "Site pro {nome}"]

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

    # Abertura humana com a nota real do Google
    if rt >= 4.7 and nr >= 50:
        abertura = (f"Oi! Vi {artigo} {nome} aqui no Google.\n"
                    f"{rt:.1f} estrelas com {nr} avaliações. Pegada séria.")
    elif rt >= 4.5 and nr >= 15:
        abertura = (f"Oi! Tava olhando {artigo} {nome} no Google, "
                    f"{rt:.1f} estrelas com {nr} avaliações.")
    elif nr > 0:
        abertura = (f"Oi! Achei {artigo} {nome} pesquisando {segmento}{em_cidade}.")
    else:
        abertura = (f"Oi! Cheguei {artigo} {nome} pesquisando {segmento}{em_cidade}.")

    # Miolo + fechamento variam por serviço
    if servico == "automacao":
        miolo = ("Você já pensou em automatizar a parte chata?\n"
                 "Prospectar cliente novo, responder WhatsApp 24h, gerar post sem precisar pensar.\n\n"
                 "A gente faz isso com IA.")
        fechamento = "Quer ver como ficaria? scoutcompany.com.br tem uns projetos lá."
    elif servico == "sistema":
        miolo = (f"Pergunta rápida: como vocês controlam cliente e agenda hoje? Caderno, planilha?\n\n"
                 f"A gente faz sistema sob medida pra {segmento}. Tudo num lugar só.")
        fechamento = "Dá uma olhada em scoutcompany.com.br. Se fizer sentido, me responde aqui."
    else:
        # site
        if "sem site" in situacao or (not site and not insta):
            miolo = ("Reparei que vocês não têm site ainda.\n"
                     f"Sei que parece detalhe, mas muita gente pesquisa {segmento}{em_cidade} antes de ir.")
            fechamento = "Posso mostrar como ficaria? Tem exemplos em scoutcompany.com.br."
        elif "só instagram" in situacao or (not site and insta):
            miolo = (f"Vi que vocês estão só no Instagram.\n"
                     "É um canal bom, mas Instagram não aparece no Google quando alguém pesquisa.\n"
                     "E cliente novo pesquisa antes de ir.")
            fechamento = "Quer ver como ficaria um site simples? scoutcompany.com.br tem uns projetos lá."
        elif "site desatualizado" in situacao or "site antigo" in situacao or "site fraco" in situacao:
            miolo = ("Dei uma olhada no site de vocês.\n"
                     "Dá pra modernizar bastante, principalmente como aparece no celular.\n"
                     "Site antigo passa impressão de negócio antigo, mesmo quando não é.")
            fechamento = "Te mando um preview de como ficaria? scoutcompany.com.br tem o estilo da gente."
        else:
            miolo = ("Olhando o digital de vocês, vi uns pontos onde dá pra melhorar.")
            fechamento = "Posso mostrar em 5 minutos. scoutcompany.com.br tem uns exemplos."

    whatsapp = f"""{abertura}

{miolo}

{fechamento}"""

    # Email — um pouco mais formal mas sem AI-fala
    if servico == "automacao":
        email_assunto = f"Automação com IA pro {nome}"
        problema_email = ("Muita coisa do dia a dia repete sempre igual. Prospecção, "
                          "primeiro atendimento no WhatsApp, criação de post. "
                          "A Scout monta automação com IA pra resolver essa parte, "
                          "sem precisar contratar ninguém.")
    elif servico == "sistema":
        email_assunto = f"Sistema de gestão pro {nome}"
        problema_email = (f"Muito negócio de {segmento} ainda controla cliente, agenda e financeiro "
                          "em caderno ou planilha. Quando o volume cresce, isso vira problema. "
                          "A Scout desenvolve sistema sob medida pra esse tipo de operação.")
    else:
        if "sem site" in situacao or (not site and not insta):
            problema_email = (f"Reparei que vocês não têm site. Quando alguém pesquisa {segmento}"
                              f"{em_cidade}, quem aparece é a concorrência. Mesmo quando o serviço "
                              "de vocês é melhor.")
        elif "só instagram" in situacao or (not site and insta):
            problema_email = (f"Vi que vocês estão só no Instagram ({insta}). É um canal bom, "
                              "mas Instagram não aparece no Google. Cliente novo pesquisa antes "
                              "de chegar.")
        else:
            problema_email = ("Dei uma olhada no digital de vocês e vi pontos onde dá pra ajudar.")
        email_assunto = f"Site pro {nome}"

    abertura_email = abertura.replace("Oi! ", "", 1).strip()
    if abertura_email:
        abertura_email = abertura_email[0].upper() + abertura_email[1:]

    email_corpo = f"""Olá!

{abertura_email}

{problema_email}

Pra conhecer uns projetos, acessa scoutcompany.com.br. Se preferir conversar por WhatsApp, é só me chamar: {assinatura_telefone}.

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
