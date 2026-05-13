"""Funções compartilhadas entre os scripts."""
import csv
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MENS_DIR = ROOT / "mensagens"
MOCK_DIR = ROOT / "mock"
LOG_DIR = ROOT / "logs"
CONVERSAS_DIR = ROOT / "conversas"

PROSPECTS_CSV = DATA_DIR / "prospects.csv"
QUALIFICADOS_CSV = DATA_DIR / "qualificados.csv"
PIPELINE_CSV = DATA_DIR / "pipeline.csv"
FILA_PATH = DATA_DIR / "fila_envio.json"
CONFIG_PATH = DATA_DIR / "config.json"
DISPAROS_LOG = LOG_DIR / "disparos.log"
VOLUME_LOG = LOG_DIR / "volume.log"

PROSPECT_FIELDS = [
    "id", "nome", "segmento", "endereco", "cidade",
    "telefone", "instagram", "site", "rating", "user_ratings_total",
    "place_id", "fonte", "coletado_em",
    # Serviço Scout que melhor encaixa pra esse prospect
    "servico_recomendado",   # site | sistema | automacao
]

QUALIFICADO_FIELDS = PROSPECT_FIELDS + [
    "score", "situacao",
    # Enriquecimento de contato (Etapa 2.5)
    "tem_whatsapp",      # sim | nao | nao_verificado
    "whatsapp_link",     # https://wa.me/55... (vazio se inválido)
    "email",             # email encontrado
    "tem_email",         # sim | nao
    "email_fonte",       # site | instagram | (vazio)
    "prioridade",        # 1=whatsapp, 2=email, 3=ligar, 4=sem_canal
]

PIPELINE_FIELDS = [
    "id", "nome", "segmento", "contato", "data_abordagem",
    "status", "observacao",
    # Serviço Scout sob o qual o prospect foi abordado
    "servico",           # site | sistema | automacao
    # Tracking do site scoutcompany.com.br
    "data_envio_site",   # quando o link foi enviado pro prospect (ISO 8601)
    "site_acessado",     # "sim" / "nao" / "" — atualização manual por enquanto
    # Follow-up automático 48h
    "data_followup",     # ISO 8601 — quando o follow-up foi disparado (ou "")
    "status_followup",   # "" | "Enviado" | "Falhou" | "Skipped"
]


def load_env():
    """Carrega .env manualmente (sem dependência externa)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


def env(key, default=None):
    return os.environ.get(key, default)


def log(msg, level="INFO"):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {level} {msg}"
    print(line, flush=True)
    with open(LOG_DIR / f"{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_csv(path, fields=None):
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})


def append_csv(path, rows, fields):
    """Adiciona linhas ao CSV (cria com header se não existir)."""
    exists = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})


def slugify(text):
    """Converte nome em slug seguro pra nome de arquivo."""
    import re
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "sem-nome"


def use_mock():
    return env("USE_MOCK", "0") == "1"


def is_truthy(val):
    return str(val).strip().lower() in ("1", "true", "sim", "yes", "y")


# ─────────────────────────────────────────────
# Blocklist de marcas grandes (~80 redes nacionais)
# Arquivo editável: data/blocklist.txt
# ─────────────────────────────────────────────
_BLOCKLIST_CACHE = None
_BLOCKLIST_PATH = DATA_DIR / "blocklist.txt"


def _load_blocklist():
    """Lê data/blocklist.txt e retorna lista de marcas em lowercase.
    Cache em memória — só relê se chamar reload_blocklist()."""
    global _BLOCKLIST_CACHE
    if _BLOCKLIST_CACHE is not None:
        return _BLOCKLIST_CACHE
    brands = []
    if _BLOCKLIST_PATH.exists():
        for line in _BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                brands.append(line.lower())
    _BLOCKLIST_CACHE = brands
    return brands


def reload_blocklist():
    """Força releitura do arquivo (útil em scripts long-running)."""
    global _BLOCKLIST_CACHE
    _BLOCKLIST_CACHE = None
    return _load_blocklist()


import re as _re


# ─────────────────────────────────────────────
# Blacklist de NÚMEROS (rejeição/opt-out individual)
# Arquivo append-only: data/blacklist_numeros.txt
# Match: dígitos do número, qualquer formato de entrada.
# ─────────────────────────────────────────────
BLACKLIST_NUMEROS_PATH = DATA_DIR / "blacklist_numeros.txt"


def _numero_digits(numero):
    """Extrai só os dígitos do número (remove +55, espaços, traços, parênteses)."""
    return "".join(c for c in str(numero or "") if c.isdigit())


def _load_blacklist_numeros():
    """Lê blacklist_numeros.txt e retorna set de strings (só dígitos).
    Não cacheia — relê todo chamada porque o arquivo é append-only e baixa volumetria."""
    if not BLACKLIST_NUMEROS_PATH.exists():
        return set()
    out = set()
    for raw in BLACKLIST_NUMEROS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # cada linha pode ser "5511999999999 # motivo" ou só o número
        token = line.split("#", 1)[0].strip()
        digits = _numero_digits(token)
        if digits:
            out.add(digits)
    return out


def is_numero_blacklisted(numero):
    """True se o número está na blacklist (case-insensitive de formato).
    Faz match nos últimos 11 dígitos (DDD+celular) pra tolerar +55 ausente/presente."""
    n = _numero_digits(numero)
    if not n:
        return False
    bl = _load_blacklist_numeros()
    if n in bl:
        return True
    # tolera diferença entre 55XXXXXXXXXXX (13 dígitos) e XXXXXXXXXXX (11 dígitos)
    if len(n) >= 11:
        tail = n[-11:]
        for b in bl:
            if b.endswith(tail) or tail.endswith(b[-11:] if len(b) >= 11 else b):
                return True
    return False


def add_numero_to_blacklist(numero, motivo=""):
    """Adiciona o número (só dígitos) na blacklist. Idempotente."""
    n = _numero_digits(numero)
    if not n:
        return False
    if is_numero_blacklisted(n):
        return False
    BLACKLIST_NUMEROS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not BLACKLIST_NUMEROS_PATH.exists():
        BLACKLIST_NUMEROS_PATH.write_text(
            "# Scout — blacklist de números individuais (rejeição/opt-out)\n"
            "# Um número por linha, só dígitos (DDI+DDD+telefone). Comentário com #.\n"
            "# Adicionado automaticamente pelo whatsapp_responder quando o prospect rejeita contato.\n"
            "# NUNCA mais contatar esses números — nem follow-up, nem novo disparo.\n\n",
            encoding="utf-8",
        )
    stamp = datetime.now().isoformat(timespec="seconds")
    suffix = f"  # {motivo} @ {stamp}" if motivo else f"  # @ {stamp}"
    with open(BLACKLIST_NUMEROS_PATH, "a", encoding="utf-8") as f:
        f.write(f"{n}{suffix}\n")
    return True


def is_blocked_brand(nome):
    """True se o nome do prospect contém uma marca da blocklist como PALAVRA INTEIRA
    (não substring). Evita falsos positivos tipo 'Extra' bater em 'Extra Ótica'
    quando o blocklist tem 'Extra Supermercado'.

    Ex: 'DROGA RAIA 0123 - Centro' bate 'droga raia' ✅
         'Extra Ótica Guarulhos' NÃO bate 'extra supermercado' ✅
         'Magazine Luiza Filial' bate 'magazine luiza' ✅"""
    if not nome:
        return False
    n = str(nome).lower()
    for brand in _load_blocklist():
        # Word boundary: marca cercada por não-letras (início, fim, espaço, traço, etc).
        # Inclui acentos no charset pra não quebrar em "ótica", "ação", etc.
        pat = r'(^|[^a-záàâãéêíóôõúüç0-9])' + _re.escape(brand) + r'($|[^a-záàâãéêíóôõúüç0-9])'
        if _re.search(pat, n):
            return True
    return False


# ═══════════════════════════════════════════════════════════
# ROTEADOR DE SERVIÇO Scout (site / sistema / automacao)
# ═══════════════════════════════════════════════════════════
# Cada segmento tem um serviço PRIMÁRIO mais provável.
# Em seguida `definir_servico_recomendado` aplica overrides por
# contexto (presença digital, volume de avaliações etc).

# Palavras-chave por serviço (substring lowercase no segmento normalizado)
SEG_KEYWORDS_SITE = (
    "restaurante", "pizzaria", "padaria", "hamburger", "hamburgueria",
    "lanchonete", "doceria", "confeitaria", "buffet",
    "salao", "salão", "barbearia", "barber", "estetica", "estética",
    "petshop", "pet shop", "pet ",
    "loja", "boutique", "otica", "ótica",
    "farmacia", "farmácia",
    "academia",  # pequenas academias = site costuma resolver
)
SEG_KEYWORDS_SISTEMA = (
    "veterinaria", "veterinária",
    "clinica", "clínica", "consultorio", "consultório", "dentista", "odonto",
    "fisioterapia",
    "studio pilates", "studio fitness",
    "escola", "creche", "colegio", "colégio", "curso",
    "imobiliaria", "imobiliária",
    "contabilidade", "contador",
    "transportadora", "logistica", "logística",
    "cooperativa",
    "auto mecanica", "oficina",  # operação com OS, peças, agenda
)
SEG_KEYWORDS_AUTOMACAO = (
    "agencia de marketing", "agência de marketing", "marketing digital",
    "consultoria",
    "advocacia", "advogado", "escritório de advocacia", "escritorio de advocacia",
    "corretora", "corretor de imoveis", "corretor de imóveis", "corretor de seguros",
    "distribuidora", "atacado", "atacadista",
    "b2b",
)


def _seg_match(seg_norm, keywords):
    return any(k in seg_norm for k in keywords)


def definir_servico_recomendado(prospect):
    """Define qual dos 3 serviços Scout encaixa melhor pra esse prospect.

    Ordem de decisão:
      1. Sem site OU site fraco/rede social   →  SITE   (independente do segmento)
      2. Segmento bate AUTOMACAO              →  AUTOMACAO
      3. Segmento bate SISTEMA + alta atividade (50+ avaliações) → SISTEMA
      4. Segmento bate SISTEMA com baixa atividade  →  SITE  (ainda precisa visibilidade)
      5. Segmento bate SITE                   →  SITE
      6. Default                              →  SITE

    Retorna string lowercase: "site" | "sistema" | "automacao"
    """
    site = (prospect.get("site") or "").strip().lower()
    insta = (prospect.get("instagram") or "").strip().lower()
    seg = (prospect.get("segmento") or "").lower()
    try:
        n_reviews = int(float(prospect.get("user_ratings_total") or 0))
    except (TypeError, ValueError):
        n_reviews = 0

    # Detecção de "site fraco" simples (URL) — duplica heurística mínima do qualify
    fraco_hints = (
        "wixsite.com", "wix.com", "canva.site", "linktr.ee", "linktree",
        "linkin.bio", "beacons.ai", "facebook.com", "instagram.com",
        "google.com/maps", "g.page", "lojaintegrada.com.br",
        "godaddysites.com", "negocio.site", "site.google.com",
    )
    site_fraco = bool(site) and any(h in site for h in fraco_hints)

    # AUTOMACAO tem precedência forte: B2B/agências geralmente já têm site
    if _seg_match(seg, SEG_KEYWORDS_AUTOMACAO):
        return "automacao"

    # Sem site OU só Instagram OU site fraco → SITE quase sempre
    if not site or site_fraco or (insta and not site):
        # Exceção: SISTEMA-fit com muito volume continua sistema mesmo sem site
        if _seg_match(seg, SEG_KEYWORDS_SISTEMA) and n_reviews >= 100:
            return "sistema"
        return "site"

    # Tem site decente: decide por segmento + atividade
    if _seg_match(seg, SEG_KEYWORDS_SISTEMA) and n_reviews >= 50:
        return "sistema"
    if _seg_match(seg, SEG_KEYWORDS_SITE):
        return "site"
    if _seg_match(seg, SEG_KEYWORDS_SISTEMA):
        return "sistema"

    # Default: SITE é a porta de entrada mais natural
    return "site"


# ═══════════════════════════════════════════════════════════
# JANELAS DE ENVIO POR SEGMENTO
# ═══════════════════════════════════════════════════════════
# Cada segmento tem 1 ou 2 janelas (hora_inicio, hora_fim) em formato 24h.
# Match no nome é por substring lowercase (ver _windows_para_segmento).
# Hard limits abaixo (HORA_MIN_GLOBAL / HORA_MAX_GLOBAL / SAB_HORA_MAX)
# clipam qualquer janela que extrapole — segurança em profundidade.
SEGMENT_WINDOWS = {
    # Comida (rush antes do almoço + antes do jantar)
    "restaurante":   [(10, 11), (15, 16)],
    "delivery":      [(10, 11), (15, 16)],
    "pizzaria":      [(10, 11), (15, 16)],
    "lanchonete":    [(10, 11), (15, 16)],
    "hamburgueria":  [(10, 11), (15, 16)],
    "padaria":       [(10, 11), (15, 16)],
    "confeitaria":   [(10, 11), (15, 16)],
    "doceria":       [(10, 11), (15, 16)],
    "buffet":        [(10, 11), (15, 16)],

    # Beleza (antes de abrir + final do dia)
    "salao":         [(9, 10), (18, 19)],
    "barbearia":     [(9, 10), (18, 19)],
    "estetica":      [(9, 10), (18, 19)],
    "beleza":        [(9, 10), (18, 19)],

    # Saúde (início do expediente + após almoço). Inclui veterinário.
    "clinica":       [(8, 9), (13, 14)],
    "consultorio":   [(8, 9), (13, 14)],
    "dentista":      [(8, 9), (13, 14)],
    "odonto":        [(8, 9), (13, 14)],
    "saude":         [(8, 9), (13, 14)],
    "hospital":      [(8, 9), (13, 14)],
    "veterinari":    [(8, 9), (13, 14)],  # "veterinario" e "veterinária"

    # Pet (varejo). NOTA: "petshop" casa antes de "pet" — match em ordem de inserção.
    "petshop":       [(10, 11), (14, 15)],
    "pet":           [(10, 11), (14, 15)],

    # Varejo geral
    "loja":          [(9, 10), (14, 15)],
    "comercio":      [(9, 10), (14, 15)],
    "otica":         [(9, 10), (14, 15)],
    "óptica":        [(9, 10), (14, 15)],

    # B2B / escritórios
    "escritorio":    [(9, 10), (14, 15)],
    "advocacia":     [(9, 10), (14, 15)],
    "advogad":       [(9, 10), (14, 15)],
    "contabilidade": [(9, 10), (14, 15)],
    "contador":      [(9, 10), (14, 15)],
    "consultoria":   [(9, 10), (14, 15)],
    "b2b":           [(9, 10), (14, 15)],
    "engenharia":    [(9, 10), (14, 15)],
    "arquitetura":   [(9, 10), (14, 15)],

    # Esporte / fitness. Spec ideal: (7,8) e (17,18); clipado em 8h pelo hard-limit
    # global, então uso 8-9. Tarde fica como spec, (17, 18).
    "academia":      [(8, 9), (17, 18)],
    "esporte":       [(8, 9), (17, 18)],
    "fitness":       [(8, 9), (17, 18)],
    "crossfit":      [(8, 9), (17, 18)],
    "pilates":       [(8, 9), (17, 18)],

    # Padrão (segmento não identificado)
    "default":       [(9, 10)],
}

# Hard limits que NUNCA são violadas (clip + skip em next_send_window)
HORA_MIN_GLOBAL = 8
HORA_MAX_GLOBAL = 19   # nunca disparar depois das 19h
SAB_HORA_MAX = 13      # sábado: só até 13h
MAX_DISPAROS_DIA_DEFAULT = 25
INTERVALO_MIN_SEG = 180  # 3 min
INTERVALO_MAX_SEG = 300  # 5 min


def _strip_accents(s):
    """Remove acentos pra match robusto: 'Estética' → 'estetica'."""
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def _windows_para_segmento(segmento):
    """Match segmento → janelas. Substring lowercase + sem acento.
    Match na ORDEM de inserção do dict (Python 3.7+) — coloque chaves
    mais específicas antes das genéricas (ex: 'petshop' antes de 'pet')."""
    if not segmento:
        return SEGMENT_WINDOWS["default"]
    s = _strip_accents(str(segmento).lower())
    for key, windows in SEGMENT_WINDOWS.items():
        if key == "default":
            continue
        if _strip_accents(key) in s:
            return windows
    return SEGMENT_WINDOWS["default"]


def next_send_window(segmento, agora=None):
    """Retorna datetime ISO da PRÓXIMA janela hábil de envio para esse segmento.

    Regras:
      - Domingo: pula pra segunda
      - Sábado após 14h: pula pra segunda
      - Antes das 8h ou depois das 20h: pula
      - Se há janela ainda hoje, usa o INÍCIO da janela
      - Se janela passou hoje, tenta próxima janela do mesmo dia ou próximo dia útil
    """
    if agora is None:
        agora = datetime.now()
    windows = _windows_para_segmento(segmento)

    for d_offset in range(0, 8):
        dia = agora + timedelta(days=d_offset)
        wd = dia.weekday()  # 0=seg ... 5=sab, 6=dom
        if wd == 6:
            continue  # domingo, nunca
        for (h_ini, h_fim) in windows:
            # respeita limite global
            h_ini_eff = max(h_ini, HORA_MIN_GLOBAL)
            h_fim_eff = min(h_fim, HORA_MAX_GLOBAL)
            if wd == 5:  # sábado
                if h_ini_eff >= SAB_HORA_MAX:
                    continue
                h_fim_eff = min(h_fim_eff, SAB_HORA_MAX)
            if h_fim_eff <= h_ini_eff:
                continue
            alvo = dia.replace(hour=h_ini_eff, minute=0, second=0, microsecond=0)
            if alvo <= agora:
                continue
            return alvo
    # fallback (não deveria chegar aqui)
    return agora + timedelta(days=1)


def is_horario_habil(agora=None):
    """True se está em janela global (8h-20h, seg-sex; sáb até 14h)."""
    if agora is None:
        agora = datetime.now()
    wd = agora.weekday()
    if wd == 6:  # domingo
        return False
    if wd == 5 and agora.hour >= SAB_HORA_MAX:
        return False
    return HORA_MIN_GLOBAL <= agora.hour < HORA_MAX_GLOBAL


# ═══════════════════════════════════════════════════════════
# FILA DE ENVIO (data/fila_envio.json)
# ═══════════════════════════════════════════════════════════
def read_fila():
    if not FILA_PATH.exists():
        return {"items": []}
    try:
        return json.loads(FILA_PATH.read_text(encoding="utf-8"))
    except Exception:
        log(f"fila corrompida em {FILA_PATH}, resetando", "WARN")
        return {"items": []}


def write_fila(fila):
    FILA_PATH.parent.mkdir(parents=True, exist_ok=True)
    FILA_PATH.write_text(json.dumps(fila, ensure_ascii=False, indent=2), encoding="utf-8")


def enqueue_dispatch(item):
    """Adiciona item à fila se ainda não estiver. Item precisa ter id e whatsapp."""
    fila = read_fila()
    ids = {x.get("id") for x in fila["items"]}
    if item.get("id") in ids:
        return False
    fila["items"].append(item)
    write_fila(fila)
    return True


def log_disparo(line):
    """Appenda no logs/disparos.log."""
    DISPAROS_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with open(DISPAROS_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {line}\n")


# ═══════════════════════════════════════════════════════════
# EVOLUTION API CLIENT
# ═══════════════════════════════════════════════════════════
EVOLUTION_URL_DEFAULT = "http://localhost:8080"
EVOLUTION_INSTANCE_DEFAULT = "scout-wa"


def _evolution_cfg():
    return {
        "url": env("EVOLUTION_URL", EVOLUTION_URL_DEFAULT).rstrip("/"),
        "apikey": env("EVOLUTION_APIKEY", "scout-evolution-key"),
        "instance": env("EVOLUTION_INSTANCE", EVOLUTION_INSTANCE_DEFAULT),
    }


def dispatch_dry_run():
    """Resolve modo de envio. Precedência:
      1) DISPATCH_MODE=LIVE → False (envia)
      2) DISPATCH_MODE=DRY  → True
      3) Fallback: SCOUT_DRY_RUN (1=dry, 0=live)
      Default: True (seguro)
    """
    mode = (env("DISPATCH_MODE", "") or "").strip().upper()
    if mode == "LIVE":
        return False
    if mode == "DRY":
        return True
    return is_truthy(env("SCOUT_DRY_RUN", "1"))


def send_whatsapp_via_evolution(numero, texto, dry_run=None):
    """Envia mensagem WhatsApp via Evolution API.

    - numero: string só com dígitos, com DDI (ex '5511940670464')
    - texto: corpo da mensagem
    - dry_run: se None, resolve via dispatch_dry_run() (DISPATCH_MODE + SCOUT_DRY_RUN).

    Retorna dict {ok: bool, status: str, response: any, dry_run: bool}.
    """
    import urllib.request as _urlreq
    cfg = _evolution_cfg()
    if dry_run is None:
        dry_run = dispatch_dry_run()

    numero_limpo = "".join(c for c in str(numero) if c.isdigit())
    if not numero_limpo:
        return {"ok": False, "status": "numero_vazio", "response": None, "dry_run": dry_run}

    if dry_run:
        log_disparo(f"DRY_RUN → {numero_limpo}: {texto[:80]!r}")
        return {"ok": True, "status": "dry_run", "response": None, "dry_run": True}

    url = f"{cfg['url']}/message/sendText/{cfg['instance']}"
    payload = json.dumps({"number": numero_limpo, "text": texto}).encode("utf-8")
    req = _urlreq.Request(
        url,
        data=payload,
        headers={"apikey": cfg["apikey"], "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlreq.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ok = 200 <= resp.status < 300
            log_disparo(f"{'OK' if ok else 'FAIL'} http={resp.status} → {numero_limpo}: {texto[:60]!r}")
            return {"ok": ok, "status": f"http_{resp.status}", "response": body, "dry_run": False}
    except Exception as e:
        log_disparo(f"FAIL exc → {numero_limpo}: {e}")
        return {"ok": False, "status": "exception", "response": str(e), "dry_run": False}


# ═══════════════════════════════════════════════════════════
# HISTÓRICO DE CONVERSAS (~/scout/conversas/[numero].json)
# ═══════════════════════════════════════════════════════════
def _conversa_path(numero):
    CONVERSAS_DIR.mkdir(parents=True, exist_ok=True)
    digits = "".join(c for c in str(numero) if c.isdigit())
    return CONVERSAS_DIR / f"{digits}.json"


def load_conversa(numero):
    p = _conversa_path(numero)
    if not p.exists():
        return {"numero": numero, "mensagens": [], "lead_quente": False, "lead_quente_em": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"numero": numero, "mensagens": [], "lead_quente": False, "lead_quente_em": None}


def save_conversa(numero, conversa):
    p = _conversa_path(numero)
    # mantém só últimas 50 mensagens em disco
    conversa["mensagens"] = conversa.get("mensagens", [])[-50:]
    p.write_text(json.dumps(conversa, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════
# CACHE IDEMPOTENTE — dedup de message_id (Evolution às vezes
# entrega o mesmo MESSAGES_UPSERT 2x; aqui bloqueamos repetição)
# ═══════════════════════════════════════════════════════════
import threading as _threading
import time as _time

_DEDUP_LOCK = _threading.Lock()
_DEDUP_MEM = {}  # {message_id: expires_at_epoch} — fallback se Redis estiver fora
_DEDUP_TTL = 60  # segundos
_REDIS_CLIENT = None
_REDIS_TRIED = False


def _get_redis():
    """Lazy connect ao Redis. Retorna None se inacessível (fallback in-memory)."""
    global _REDIS_CLIENT, _REDIS_TRIED
    if _REDIS_TRIED:
        return _REDIS_CLIENT
    _REDIS_TRIED = True
    url = env("REDIS_URL") or "redis://redis:6379/0"
    try:
        import redis as _redis_lib
        client = _redis_lib.Redis.from_url(url, socket_connect_timeout=2,
                                           socket_timeout=2, decode_responses=True)
        client.ping()
        _REDIS_CLIENT = client
        log(f"Redis conectado: {url}")
    except Exception as e:
        log(f"Redis indisponível ({e}) — usando dedup in-memory", "WARN")
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


def mensagem_ja_processada(message_id):
    """Marca message_id como visto. Retorna True se JÁ estava no cache
    (duplicado — caller deve ignorar). False se é a primeira vez (caller processa).

    Atomicidade garantida via Redis SETNX (cross-process). Em fallback in-memory,
    usa lock local — single-process apenas.
    """
    if not message_id:
        return False  # sem ID, processa (não tem como deduplicar)

    redis_cli = _get_redis()
    if redis_cli is not None:
        try:
            key = f"processed:{message_id}"
            # SET com NX=True (só seta se não existe) + EX=TTL — atomic
            ok = redis_cli.set(key, "1", nx=True, ex=_DEDUP_TTL)
            return not ok  # ok=True => primeira vez (não duplicada); ok=None/False => já existia
        except Exception as e:
            log(f"Redis dedup falhou ({e}), caindo pra in-memory", "WARN")
            # cai pro fallback

    # Fallback in-memory (single-process)
    now = _time.time()
    with _DEDUP_LOCK:
        # GC de entradas expiradas (mantém map pequeno)
        if len(_DEDUP_MEM) > 1000:
            _DEDUP_MEM_NEW = {k: v for k, v in _DEDUP_MEM.items() if v > now}
            _DEDUP_MEM.clear()
            _DEDUP_MEM.update(_DEDUP_MEM_NEW)
        if message_id in _DEDUP_MEM and _DEDUP_MEM[message_id] > now:
            return True  # duplicada
        _DEDUP_MEM[message_id] = now + _DEDUP_TTL
        return False  # primeira vez


# ═══════════════════════════════════════════════════════════
# ESCALONAMENTO DE VOLUME (aquecimento do número)
# ═══════════════════════════════════════════════════════════
# Semana 1 (dias 1-7):   25/dia — aquecimento
# Semana 2 (dias 8-14):  50/dia
# Semana 3 (dias 15-21): 80/dia
# Semana 4+ (dia 22+):   100/dia
# Auto-throttle: se falha > 30% no dia ANTERIOR, reduz 20% no dia atual
VOLUME_POR_SEMANA = {1: 25, 2: 50, 3: 80, 4: 100}
THROTTLE_FALHA_PCT = 30.0
THROTTLE_REDUCAO = 0.20


def read_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def marcar_primeiro_disparo_se_preciso():
    """Se ainda não há primeiro_disparo registrado, grava hoje.
    Chamado dentro do dispatcher quando há tentativa real."""
    cfg = read_config()
    if not cfg.get("primeiro_disparo"):
        cfg["primeiro_disparo"] = datetime.now().date().isoformat()
        write_config(cfg)
    return cfg.get("primeiro_disparo")


# ═══════════════════════════════════════════════════════════
# DDDs BR válidos (Anatel) — usado pra filtrar telefones plausíveis
# ═══════════════════════════════════════════════════════════
DDDS_BR_VALIDOS = frozenset({
    # Sudeste
    11, 12, 13, 14, 15, 16, 17, 18, 19,         # SP
    21, 22, 24,                                  # RJ
    27, 28,                                      # ES
    31, 32, 33, 34, 35, 37, 38,                  # MG
    # Sul
    41, 42, 43, 44, 45, 46,                      # PR
    47, 48, 49,                                  # SC
    51, 53, 54, 55,                              # RS
    # Centro-Oeste
    61,                                          # DF
    62, 64,                                      # GO
    63,                                          # TO
    65, 66,                                      # MT
    67,                                          # MS
    # Nordeste
    68, 69,                                      # AC/RO
    71, 73, 74, 75, 77,                          # BA
    79,                                          # SE
    81, 87,                                      # PE
    82,                                          # AL
    83,                                          # PB
    84,                                          # RN
    85, 88,                                      # CE
    86, 89,                                      # PI
    98, 99,                                      # MA
    # Norte
    91, 93, 94,                                  # PA
    92, 97,                                      # AM
    96,                                          # AP
    95,                                          # RR
})


def ddd_br_valido(ddd):
    """True se DDD está na lista oficial Anatel."""
    try:
        return int(ddd) in DDDS_BR_VALIDOS
    except (TypeError, ValueError):
        return False


# ═══════════════════════════════════════════════════════════
# RODÍZIO DE CIDADES (busca em São Paulo expandido)
# ═══════════════════════════════════════════════════════════
CIDADES_RODIZIO_DEFAULT = [
    "São Paulo", "Guarulhos", "Campinas", "Santo André",
    "São Bernardo do Campo", "Osasco", "Sorocaba", "Ribeirão Preto",
    "São José dos Campos", "Santos", "Mauá", "Mogi das Cruzes",
    "Diadema", "Carapicuíba", "Itaquaquecetuba",
]


def _ler_lista_cidades():
    raw = env("CIDADES_RODIZIO", "")
    if not raw:
        return list(CIDADES_RODIZIO_DEFAULT)
    cidades = [c.strip() for c in raw.split(",") if c.strip()]
    return cidades or list(CIDADES_RODIZIO_DEFAULT)


def proximas_cidades_rodizio(n=None):
    """Devolve as próximas `n` cidades em rodízio circular e ATUALIZA config.json
    para a próxima chamada começar onde parou.

    Estrutura em config.json:
      "rodizio": {
        "ultimo_indice_fim": int,        # índice (exclusivo) onde a última rodada parou
        "ultimas_cidades": [str, ...],   # cidades que foram usadas na última rodada
        "atualizado_em": ISO
      }
    """
    cidades = _ler_lista_cidades()
    if n is None:
        try:
            n = int(env("CIDADES_POR_RODADA", "3"))
        except Exception:
            n = 3
    n = max(1, min(n, len(cidades)))

    cfg = read_config()
    rod = cfg.get("rodizio", {})
    start = int(rod.get("ultimo_indice_fim", 0)) % len(cidades)

    selecionadas = []
    idx = start
    for _ in range(n):
        selecionadas.append(cidades[idx])
        idx = (idx + 1) % len(cidades)

    cfg["rodizio"] = {
        "ultimo_indice_fim": idx,
        "ultimas_cidades": selecionadas,
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        "total_cidades_no_pool": len(cidades),
    }
    write_config(cfg)
    return selecionadas


def calcular_semana_atual():
    """Retorna 1, 2, 3 ou 4 (cap em 4).
    Se ainda não houve primeiro_disparo, retorna 1."""
    cfg = read_config()
    pd = cfg.get("primeiro_disparo")
    if not pd:
        return 1
    try:
        d0 = datetime.fromisoformat(pd).date()
    except Exception:
        try:
            d0 = datetime.strptime(pd, "%Y-%m-%d").date()
        except Exception:
            return 1
    delta_dias = (datetime.now().date() - d0).days
    # dia 1-7 → semana 1, dia 8-14 → semana 2, ...
    semana = (delta_dias // 7) + 1
    return min(max(semana, 1), 4)


def _stats_volume_dia(data_iso):
    """Conta tentativas/sucesso/falha do dia data_iso (YYYY-MM-DD) lendo disparos.log."""
    if not DISPAROS_LOG.exists():
        return {"tentativas": 0, "sucesso": 0, "falha": 0, "dryrun": 0}
    s = {"tentativas": 0, "sucesso": 0, "falha": 0, "dryrun": 0}
    with open(DISPAROS_LOG, encoding="utf-8") as f:
        for line in f:
            if not line.startswith(f"[{data_iso}"):
                continue
            if " RODADA " in line:
                continue
            if "OK " in line:
                s["sucesso"] += 1
                s["tentativas"] += 1
            elif "FAIL " in line:
                s["falha"] += 1
                s["tentativas"] += 1
            elif "DRY_RUN " in line:
                s["dryrun"] += 1
                s["tentativas"] += 1
    return s


def calcular_max_disparos_hoje():
    """Retorna o limite ajustado para hoje considerando:
      - Semana de aquecimento
      - Auto-throttle: -20% se falha > 30% no dia anterior

    Override via env MAX_DISPAROS_DIA → ignora cálculo automático.
    """
    override = env("MAX_DISPAROS_DIA")
    if override and override.isdigit():
        return int(override)

    semana = calcular_semana_atual()
    base = VOLUME_POR_SEMANA.get(semana, VOLUME_POR_SEMANA[4])

    # Throttle: olha dia anterior
    ontem = (datetime.now().date() - timedelta(days=1)).isoformat()
    stats_ontem = _stats_volume_dia(ontem)
    if stats_ontem["tentativas"] >= 5:  # só aplica se houver volume mínimo
        pct_falha = 100.0 * stats_ontem["falha"] / stats_ontem["tentativas"]
        if pct_falha > THROTTLE_FALHA_PCT:
            reduzido = int(base * (1 - THROTTLE_REDUCAO))
            log(f"AUTO-THROTTLE: falha ontem {pct_falha:.0f}% > {THROTTLE_FALHA_PCT}% "
                f"→ reduzindo {base} → {reduzido}", "WARN")
            return reduzido
    return base


def registrar_volume_dia(extras=None):
    """Appenda no volume.log um resumo do dia atual.

    extras: dict opcional de campos adicionais ('reducao', 'dry_run', etc).
    """
    VOLUME_LOG.parent.mkdir(parents=True, exist_ok=True)
    hoje = datetime.now().date().isoformat()
    semana = calcular_semana_atual()
    stats = _stats_volume_dia(hoje)
    max_dia = calcular_max_disparos_hoje()
    parts = [
        f"[{hoje}]",
        f"semana={semana}",
        f"max={max_dia}",
        f"tentativas={stats['tentativas']}",
        f"sucesso={stats['sucesso']}",
        f"falha={stats['falha']}",
        f"dryrun={stats['dryrun']}",
    ]
    if extras:
        for k, v in extras.items():
            parts.append(f"{k}={v}")
    line = " ".join(parts) + "\n"
    with open(VOLUME_LOG, "a", encoding="utf-8") as f:
        f.write(line)
