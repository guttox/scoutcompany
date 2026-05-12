"""
Etapa 2.5 — enriquece prospects qualificados com validação de canais.

VALIDAÇÃO DE WHATSAPP (heurística honesta):
  - Não existe API pública gratuita que valide se um número TEM WhatsApp
    sem violar ToS. wa.me/X retorna 200 pra qualquer formato válido.
  - Heurística usada (Brasil):
      * Celular (DDD + 9 + 8 dígitos = 11 dígitos) → tem_whatsapp=sim (PRESUMIDO,
        ~95% dos celulares BR têm WhatsApp)
      * Fixo (DDD + 8 dígitos sem o 9 inicial)    → tem_whatsapp=nao_verificado
      * Formato inválido / ausente               → tem_whatsapp=nao
  - whatsapp_link só preenchido quando tem_whatsapp=sim.

VALIDAÇÃO DE EMAIL:
  - Scraping do site do prospect (paginas comuns: /, /contato, /fale-conosco)
  - Regex de email + DNS A-record check no domínio
  - Filtra emails de plataforma (no-reply, sentry, wix, formulários)
  - Instagram bio: best-effort (Meta bloqueia bots; provavelmente vai falhar)
  - Google search NÃO é usado (precisa Custom Search API paga)

PRIORIDADE:
  1 = WhatsApp (tem_whatsapp=sim)
  2 = Email (tem_email=sim, sem WA)
  3 = Telefone (tem telefone, sem WA, sem email)
  4 = Sem canal (nada)

Uso:
  python3 enrich_contacts.py
"""
import re
import socket
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    QUALIFICADOS_CSV, QUALIFICADO_FIELDS,
    env, load_env, log, read_csv, write_csv,
)

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)

# Domínios de email que NUNCA devem ser tratados como contato real do negócio
EMAIL_BLOCKLIST_DOMAINS = (
    "wixpress.com", "wix.com", "sentry.io", "googleapis.com",
    "example.com", "domain.com", "email.com",
)
# Locais que indicam email genérico de plataforma, não do negócio
EMAIL_BLOCKLIST_LOCALS = (
    "no-reply", "noreply", "donotreply", "wordpress",
)

REQUEST_TIMEOUT = 6
USER_AGENT = "Mozilla/5.0 (Scout/1.0; +https://scout.local)"


# =====================================================
# WhatsApp — heurística por formato BR
# =====================================================

def normalize_phone_br(phone):
    """
    Recebe '+55 11 94067-0464' e retorna ('5511940670464', tipo).
    tipo ∈ {'mobile', 'landline', 'invalid'}.
    Regras:
      - 11 dígitos com DDD + 9-prefix = mobile
      - 10 dígitos com DDD = landline
      - outro formato = invalid
    """
    if not phone:
        return None, "invalid"
    digits = re.sub(r"\D", "", phone)
    # Remove código do país 55 se presente
    if digits.startswith("55") and len(digits) > 11:
        digits = digits[2:]
    if len(digits) == 11 and digits[2] == "9":
        return "55" + digits, "mobile"
    if len(digits) == 10:
        return "55" + digits, "landline"
    return None, "invalid"


def validar_whatsapp(phone):
    """
    Retorna (status, link).
    status ∈ {'sim', 'nao', 'nao_verificado'}
    link = 'https://wa.me/55...' ou ''.
    """
    canonical, tipo = normalize_phone_br(phone)
    if tipo == "mobile":
        return "sim", f"https://wa.me/{canonical}"
    if tipo == "landline":
        return "nao_verificado", ""
    return "nao", ""


# =====================================================
# Email — scraping de site
# =====================================================

def fetch(url, timeout=REQUEST_TIMEOUT):
    """HTTP GET simples retornando texto. None em falha."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "text" not in ctype and "html" not in ctype and ctype != "":
                return None
            data = resp.read(500_000)  # limite 500KB
            try:
                return data.decode("utf-8", errors="ignore")
            except Exception:
                return None
    except Exception as e:
        log(f"  fetch falhou ({url}): {e}", "DEBUG")
        return None


def domain_exists(domain):
    """Checa se domínio resolve via DNS (gethostbyname)."""
    try:
        socket.gethostbyname(domain)
        return True
    except (socket.gaierror, socket.herror):
        return False


def email_valido(email):
    """Valida formato + domínio existente + não está em blocklist."""
    if not email or "@" not in email:
        return False
    local, _, domain = email.partition("@")
    domain = domain.lower()
    if domain in EMAIL_BLOCKLIST_DOMAINS:
        return False
    for bl in EMAIL_BLOCKLIST_LOCALS:
        if bl in local.lower():
            return False
    if not domain_exists(domain):
        return False
    return True


def extrair_emails(html):
    """Extrai emails de um HTML (texto + mailto:)."""
    if not html:
        return []
    emails = set(EMAIL_REGEX.findall(html))
    # Mailto: também
    for m in re.findall(r'mailto:([^"\'\s>]+)', html, re.IGNORECASE):
        if "@" in m:
            emails.add(m.split("?")[0])
    return list(emails)


def buscar_email_no_site(site_url):
    """Tenta encontrar email no site do prospect.
    Visita home + /contato + /fale-conosco. Retorna o melhor email encontrado."""
    if not site_url:
        return None
    base = site_url.strip()
    if not base.startswith("http"):
        base = "https://" + base
    base = base.rstrip("/")

    candidate_paths = ["", "/contato", "/contact", "/fale-conosco", "/sobre", "/about"]
    found = []
    for path in candidate_paths:
        url = base + path
        html = fetch(url)
        if html:
            for e in extrair_emails(html):
                e = e.strip().rstrip(".,;)").lower()
                if e not in found and email_valido(e):
                    found.append(e)
        if found:
            break  # já achou, não precisa visitar mais paths

    if not found:
        return None

    # Prioriza emails "contato@", "atendimento@", etc
    preferred_locals = ("contato", "atendimento", "comercial", "sac", "vendas", "info", "hello", "ola")
    for pref in preferred_locals:
        for e in found:
            if e.split("@")[0].startswith(pref):
                return e
    return found[0]


def buscar_email_no_instagram(handle):
    """Best-effort. Instagram bloqueia scraping logado, mas raspamos o HTML
    público — pode pegar o email do og:description em algumas raras contas
    com email exposto. Maioria das tentativas vai falhar e isso é esperado."""
    if not handle:
        return None
    handle = handle.strip().lstrip("@")
    if not handle:
        return None
    url = f"https://www.instagram.com/{handle}/"
    html = fetch(url, timeout=4)
    if not html:
        return None
    for e in extrair_emails(html):
        e = e.strip().rstrip(".,;)").lower()
        if email_valido(e):
            return e
    return None


# =====================================================
# Prioridade
# =====================================================

def calcular_prioridade(tem_whatsapp, tem_email, telefone):
    if tem_whatsapp == "sim":
        return "1"
    if tem_email == "sim":
        return "2"
    if telefone:
        return "3"
    return "4"


# =====================================================
# Main
# =====================================================

def enrich_one(prospect):
    """Enriquece um prospect in-place. Retorna o dict modificado."""
    nome = prospect.get("nome", "")
    log(f"  Enriquecendo: {nome}")

    # WhatsApp
    tel = prospect.get("telefone", "")
    wa_status, wa_link = validar_whatsapp(tel)
    prospect["tem_whatsapp"] = wa_status
    prospect["whatsapp_link"] = wa_link

    # Email — site primeiro, instagram como fallback
    site = prospect.get("site", "")
    insta = prospect.get("instagram", "")
    email = None
    fonte = ""
    if site:
        email = buscar_email_no_site(site)
        if email:
            fonte = "site"
    if not email and insta:
        email = buscar_email_no_instagram(insta)
        if email:
            fonte = "instagram"

    prospect["email"] = email or ""
    prospect["tem_email"] = "sim" if email else "nao"
    prospect["email_fonte"] = fonte

    # Prioridade
    prospect["prioridade"] = calcular_prioridade(
        prospect["tem_whatsapp"], prospect["tem_email"], tel
    )

    return prospect


def main():
    load_env()
    qualificados = read_csv(QUALIFICADOS_CSV)
    if not qualificados:
        log("Nenhum prospect em qualificados.csv. Rode qualify.py primeiro.", "WARN")
        return

    log(f"Enriquecendo {len(qualificados)} prospects...")

    # Estatísticas
    counters = {"wa_sim": 0, "wa_nao": 0, "wa_nv": 0,
                "email_sim": 0, "email_nao": 0,
                "p1": 0, "p2": 0, "p3": 0, "p4": 0}

    for p in qualificados:
        enrich_one(p)
        wa = p.get("tem_whatsapp")
        em = p.get("tem_email")
        pr = p.get("prioridade")
        counters[f"wa_{'sim' if wa=='sim' else 'nao' if wa=='nao' else 'nv'}"] += 1
        counters[f"email_{em}"] = counters.get(f"email_{em}", 0) + 1
        counters[f"p{pr}"] = counters.get(f"p{pr}", 0) + 1

    write_csv(QUALIFICADOS_CSV, qualificados, QUALIFICADO_FIELDS)
    log(f"✅ Enriquecimento salvo em {QUALIFICADOS_CSV}")

    log(f"📱 WhatsApp — sim: {counters['wa_sim']} | nao: {counters['wa_nao']} | nao_verificado: {counters['wa_nv']}")
    log(f"📧 Email    — sim: {counters['email_sim']} | nao: {counters['email_nao']}")
    log(f"🎯 Prioridade — P1(WA): {counters['p1']} | P2(email): {counters['p2']} | "
        f"P3(ligar): {counters['p3']} | P4(sem canal): {counters['p4']}")


if __name__ == "__main__":
    main()
