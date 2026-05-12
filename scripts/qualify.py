"""
Etapa 2 — qualifica prospects por score 1-10.

Regras (fonte: briefing Augusto):
- Sem site nenhum:                    +4
- Site presente mas marcado "antigo": +3
- Site fraco/genérico (heurística):   +2
- Só Instagram (tem IG, não tem site):+3
- Avaliação Google >= 4.0:            +2
- Mais de 50 avaliações:              +1
- Telefone disponível:                +2

Cap em 10. Salva score >= SCORE_MIN em qualificados.csv.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    DATA_DIR, PROSPECTS_CSV, QUALIFICADOS_CSV, QUALIFICADO_FIELDS,
    env, is_blocked_brand, load_env, log, read_csv, write_csv,
)

APRENDIZADOS_PATH = DATA_DIR / "aprendizados.json"


def carregar_ajustes_aprendizado():
    """Lê data/aprendizados.json e devolve dict normalizado:
        {
          "priorizar_segmentos": {nome_seg_lower: boost_int},
          "icp_segmentos_lower": [..],
          "icp_score_boost": int,
          "icp_rating_min": float|None,
        }
    Vazio se arquivo não existe.
    """
    import json
    out = {
        "priorizar_segmentos": {},
        "icp_segmentos_lower": [],
        "icp_score_boost": 0,
        "icp_rating_min": None,
    }
    if not APRENDIZADOS_PATH.exists():
        return out
    try:
        d = json.loads(APRENDIZADOS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return out
    for aj in d.get("ajustes_aplicados", []):
        if aj.get("tipo") == "priorizar_segmentos":
            boost = int(aj.get("score_boost", 1))
            for s in aj.get("segmentos", []):
                out["priorizar_segmentos"][s.lower()] = boost
        elif aj.get("tipo") == "icp_match":
            out["icp_segmentos_lower"] = [s.lower() for s in aj.get("segmentos_icp", [])]
            out["icp_score_boost"] = int(aj.get("score_boost", 2))
            if aj.get("rating_min") is not None:
                try:
                    out["icp_rating_min"] = float(aj["rating_min"])
                except Exception:
                    pass
    return out


def aplicar_boost_aprendizado(p, score, motivos, ajustes):
    """Aplica boost ao score baseado em segmento campeão / match ICP."""
    seg = (p.get("segmento") or "").strip().lower()
    try:
        rating = float(p.get("rating") or 0)
    except Exception:
        rating = 0
    # Boost segmento campeão (taxa de resposta alta na última semana)
    if seg and seg in ajustes["priorizar_segmentos"]:
        b = ajustes["priorizar_segmentos"][seg]
        score += b
        motivos.append(f"+{b} segmento campeão")
    # ICP match — só aplica se rating bate o mínimo (ou se ICP não exige)
    if seg and seg in ajustes["icp_segmentos_lower"]:
        if ajustes["icp_rating_min"] is None or rating >= ajustes["icp_rating_min"]:
            b = ajustes["icp_score_boost"]
            score += b
            motivos.append(f"+{b} match ICP")
    return score, motivos


SITE_FRACO_HINTS = (
    # Builders sem domínio próprio (claramente fracos)
    "wixsite.com", "wix.com",
    "canva.site", "my.canva.site",
    "myportfolio.com",
    "weebly.com", "webnode.com.br", "webnode.com",
    "godaddysites.com",
    "negocio.site",
    "site.google.com", "sites.google.com",
    # Redes sociais usadas como "site"
    "linktr.ee", "linktree", "linkin.bio", "beacons.ai",
    "facebook.com", "instagram.com", "fb.com",
    "google.com/maps", "maps.google", "g.page",
    # Plataformas de loja básicas (não custom)
    "lojaintegrada.com.br", "yampi.com.br", "br.beepy", "loja.cocoutiu.com",
    # Builders genéricos
    "uolhost.com.br", "weebly", "jimdosite.com",
)

# Markers no HTML que indicam builder usado (caso URL seja custom domain)
SITE_FRACO_HTML_MARKERS = (
    'wix.com website builder', 'wix-site',
    'canva.com', 'made with canva',
    'godaddy', 'godaddy.com',
    'weebly', 'made with weebly',
    'webnode', 'powered by webnode',
    'jimdo', 'powered by jimdo',
    # Wordpress com tema gratuito básico (sinal de "feito em casa")
    'astra-starter-templates',
    'hello elementor',
    # Lojas básicas
    'tray.com.br',
)

_HTML_CHECK_CACHE = {}


def is_site_fraco(site):
    """Detecta site fraco via URL (rápido, sem HTTP)."""
    if not site:
        return False
    s = site.lower()
    return any(h in s for h in SITE_FRACO_HINTS)


def is_site_fraco_via_html(site, timeout=4):
    """Fetch HEAD/GET parcial pra detectar builder via <meta name="generator">
       ou outros markers. Resultado cacheado por URL."""
    if not site:
        return False
    if site in _HTML_CHECK_CACHE:
        return _HTML_CHECK_CACHE[site]
    import urllib.request
    try:
        url = site if site.startswith("http") else "https://" + site
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Scout/1.0; +https://scoutcompany.com.br)",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype.lower() and ctype != "":
                _HTML_CHECK_CACHE[site] = False
                return False
            # Lê só os primeiros 30KB — meta generator vem no <head>
            data = resp.read(30_000).decode("utf-8", errors="ignore").lower()
        for marker in SITE_FRACO_HTML_MARKERS:
            if marker in data:
                _HTML_CHECK_CACHE[site] = True
                return True
        _HTML_CHECK_CACHE[site] = False
        return False
    except Exception:
        _HTML_CHECK_CACHE[site] = False
        return False


def score_prospect(p, mock_data_lookup=None, ajustes_aprendizado=None):
    """Retorna (score, situacao_label)."""
    score = 0
    motivos = []

    site = (p.get("site") or "").strip()
    insta = (p.get("instagram") or "").strip()
    tel = (p.get("telefone") or "").strip()

    try:
        rating = float(p.get("rating") or 0)
    except (ValueError, TypeError):
        rating = 0.0
    try:
        n_reviews = int(float(p.get("user_ratings_total") or 0))
    except (ValueError, TypeError):
        n_reviews = 0

    # Lookup no mock pra checar site_status (apenas em mock mode)
    site_status_mock = ""
    if mock_data_lookup is not None:
        m = mock_data_lookup.get(p.get("place_id", ""))
        if m:
            site_status_mock = m.get("site_status", "")

    if not site:
        if insta:
            score += 3
            motivos.append("Só Instagram")
        else:
            score += 4
            motivos.append("Sem site")
    else:
        # Tem site — verifica se é fraco (URL pattern primeiro, depois HTML markers)
        fraco = is_site_fraco(site)
        fonte_fraco = "URL"
        if not fraco and site_status_mock != "ok":
            # Fetch HTML pra ver builder (só se URL pattern não detectou)
            fraco = is_site_fraco_via_html(site)
            fonte_fraco = "HTML"
        if site_status_mock == "antigo" or fraco:
            score += 3
            motivos.append(f"Site fraco ({fonte_fraco})" if fraco else "Site desatualizado")
        elif site_status_mock == "ok":
            motivos.append("Site OK")
        # site decente — não pontua aqui

    if rating >= 4.0:
        score += 2
        motivos.append(f"Rating {rating:.1f}")
    if n_reviews > 50:
        score += 1
        motivos.append(f"{n_reviews} avaliações")
    if tel:
        score += 2

    # Boosts baseados em aprendizado contínuo (ICP + segmentos campeões)
    if ajustes_aprendizado:
        score, motivos = aplicar_boost_aprendizado(p, score, motivos, ajustes_aprendizado)

    score = min(score, 10)
    situacao = " · ".join(motivos) if motivos else "Sem dados"
    return score, situacao


def load_mock_lookup():
    """Carrega o mock dataset pra ter acesso ao campo extra `site_status`."""
    import json
    from _common import MOCK_DIR
    mock_file = MOCK_DIR / "guarulhos_sample.json"
    if not mock_file.exists():
        return {}
    data = json.loads(mock_file.read_text(encoding="utf-8"))
    return {p["place_id"]: p for p in data.get("prospects", []) if p.get("place_id")}


def main():
    load_env()
    score_min = int(env("SCORE_MIN", "6"))

    prospects = read_csv(PROSPECTS_CSV)
    if not prospects:
        log("Nenhum prospect em prospects.csv. Rode search_prospects.py primeiro.", "WARN")
        return []

    # FILTRO DEFENSIVO: tira marcas grandes (defesa em profundidade — search já filtra,
    # mas se algum entrou antes do filtro existir, aqui pega também)
    total = len(prospects)
    prospects = [p for p in prospects if not is_blocked_brand(p.get("nome", ""))]
    blocked = total - len(prospects)
    if blocked > 0:
        log(f"🚫 {blocked} marca(s) grande(s) ignorada(s) (blocklist)")

    log(f"Qualificando {len(prospects)} prospects (cutoff={score_min})")

    mock_lookup = load_mock_lookup()
    ajustes = carregar_ajustes_aprendizado()
    if ajustes["priorizar_segmentos"] or ajustes["icp_segmentos_lower"]:
        log(f"📚 Aplicando aprendizado: segmentos campeões={list(ajustes['priorizar_segmentos'].keys())} · "
            f"ICP={ajustes['icp_segmentos_lower']}")
    qualificados = []
    distribuicao = {}
    for p in prospects:
        score, situacao = score_prospect(p, mock_lookup, ajustes_aprendizado=ajustes)
        p_q = dict(p)
        p_q["score"] = score
        p_q["situacao"] = situacao
        # Inicializa campos de enrichment vazios — preenchidos por enrich_contacts.py
        p_q.setdefault("tem_whatsapp", "")
        p_q.setdefault("whatsapp_link", "")
        p_q.setdefault("email", "")
        p_q.setdefault("tem_email", "")
        p_q.setdefault("email_fonte", "")
        p_q.setdefault("prioridade", "")
        distribuicao[score] = distribuicao.get(score, 0) + 1
        if score >= score_min:
            qualificados.append(p_q)

    log(f"Distribuição de scores: {dict(sorted(distribuicao.items(), reverse=True))}")
    log(f"Qualificados (score>={score_min}): {len(qualificados)}")

    # Ordena por score desc
    qualificados.sort(key=lambda x: (-int(x["score"]), x["nome"]))

    write_csv(QUALIFICADOS_CSV, qualificados, QUALIFICADO_FIELDS)
    log(f"✅ Salvos em {QUALIFICADOS_CSV}")

    print(f"QUALIFICADOS={len(qualificados)}")
    return qualificados


if __name__ == "__main__":
    main()
