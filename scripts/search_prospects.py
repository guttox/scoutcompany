"""
Etapa 1 — busca prospects via Google Places API.
Fallback: dataset mock quando GOOGLE_PLACES_KEY não está configurada.

Uso:
  python3 search_prospects.py                # usa LOCALIZACAO_PADRAO do .env
  python3 search_prospects.py --max 20       # limita a 20 prospects
  python3 search_prospects.py --segmentos restaurante,clinica
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    DATA_DIR, MOCK_DIR, PROSPECTS_CSV, PROSPECT_FIELDS,
    append_csv, definir_servico_recomendado, env, is_blocked_brand,
    load_env, log, proximas_cidades_rodizio, slugify, use_mock,
)

DEFAULT_SEGMENTOS = [
    # Alimentação
    "restaurante", "pizzaria", "padaria artesanal", "confeitaria artesanal",
    "hamburgueria",
    # Saúde
    "clinica odontologica", "clinica medica", "clinica estetica",
    "clinica de beleza",
    "petshop", "veterinaria",
    # Beleza
    "salao de beleza", "barbearia",
    # Fitness
    "academia", "studio pilates",
    # Varejo
    "loja de roupas", "otica",
    "farmacia independente",
    # Serviços
    "imobiliaria",
    "contabilidade",
    "advocacia",
    "auto mecanica", "oficina mecanica",
    # Educação
    "escola de idiomas", "curso livre", "escola particular",
    # Construção/imobiliário
    "construtora pequena",
    # Criativos
    "fotografo profissional", "estudio de fotografia",
]


def fetch_real(segmentos, localizacao, raio_km, max_results, per_segment=None):
    """Busca real via Google Places API (Text Search + Place Details)."""
    try:
        import googlemaps  # type: ignore
    except ImportError:
        log("googlemaps não instalado. Rode: pip3 install googlemaps", "ERROR")
        return []

    key = env("GOOGLE_PLACES_KEY")
    if not key:
        log("GOOGLE_PLACES_KEY ausente — caindo para mock", "WARN")
        return fetch_mock(segmentos, max_results)

    client = googlemaps.Client(key=key)
    log(f"Buscando real: localizacao={localizacao}, raio={raio_km}km, max={max_results}")

    geocode = client.geocode(localizacao)
    if not geocode:
        log(f"Não consegui geocodificar '{localizacao}'", "ERROR")
        return []
    loc = geocode[0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]

    seen = set()
    results = []
    for seg in segmentos:
        if len(results) >= max_results:
            break
        try:
            response = client.places(
                query=f"{seg} {localizacao}",
                location=(lat, lng),
                radius=raio_km * 1000,
            )
        except Exception as e:
            log(f"Falha em places('{seg}'): {e}", "WARN")
            continue

        seg_count = 0
        for place in response.get("results", []):
            if per_segment and seg_count >= per_segment:
                break
            place_id = place.get("place_id")
            if not place_id or place_id in seen:
                continue
            seen.add(place_id)
            seg_count += 1

            try:
                details = client.place(
                    place_id,
                    fields=[
                        "name", "formatted_address", "formatted_phone_number",
                        "international_phone_number", "website", "rating",
                        "user_ratings_total", "url",
                    ],
                ).get("result", {})
            except Exception as e:
                log(f"Falha em place_details('{place_id}'): {e}", "WARN")
                details = {}

            phone = details.get("international_phone_number") or details.get("formatted_phone_number") or ""
            results.append({
                "nome": details.get("name") or place.get("name", ""),
                "segmento": seg.title(),
                "endereco": details.get("formatted_address") or place.get("formatted_address", ""),
                "cidade": _extract_cidade(details.get("formatted_address") or "", localizacao),
                "telefone": phone,
                "instagram": "",   # Google Places não retorna IG; fica em branco
                "site": details.get("website") or "",
                "rating": details.get("rating") or place.get("rating", 0),
                "user_ratings_total": details.get("user_ratings_total") or place.get("user_ratings_total", 0),
                "place_id": place_id,
                "fonte": "google_places",
            })
            if len(results) >= max_results:
                break

    return results


def fetch_real_por_cotas(cotas, localizacao, raio_km):
    """Busca via Google Places respeitando cotas por categoria.

    cotas = [{"categoria": "...", "cota": N, "queries": [...]}, ...]

    Itera categorias e dentro de cada uma rotaciona as queries até atingir
    a cota daquela categoria (ou esgotar as queries). Retorna lista de
    prospects, cada um marcado com o campo `categoria` herdado da bucket.
    """
    try:
        import googlemaps  # type: ignore
    except ImportError:
        log("googlemaps não instalado. Rode: pip3 install googlemaps", "ERROR")
        return []

    key = env("GOOGLE_PLACES_KEY")
    if not key:
        log("GOOGLE_PLACES_KEY ausente — caindo para mock", "WARN")
        return []

    client = googlemaps.Client(key=key)
    log(f"Buscando real POR COTAS: localizacao={localizacao}, raio={raio_km}km")

    geocode = client.geocode(localizacao)
    if not geocode:
        log(f"Não consegui geocodificar '{localizacao}'", "ERROR")
        return []
    loc = geocode[0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]

    seen = set()
    results = []
    contagem_por_categoria = {}

    for bucket in cotas:
        categoria = bucket["categoria"]
        cota_alvo = int(bucket.get("cota", 0))
        queries = bucket.get("queries") or []
        cat_count = 0

        for query in queries:
            if cat_count >= cota_alvo:
                break
            try:
                response = client.places(
                    query=f"{query} {localizacao}",
                    location=(lat, lng),
                    radius=raio_km * 1000,
                )
            except Exception as e:
                log(f"Falha em places('{query}'): {e}", "WARN")
                continue

            for place in response.get("results", []):
                if cat_count >= cota_alvo:
                    break
                place_id = place.get("place_id")
                if not place_id or place_id in seen:
                    continue
                seen.add(place_id)

                try:
                    details = client.place(
                        place_id,
                        fields=[
                            "name", "formatted_address", "formatted_phone_number",
                            "international_phone_number", "website", "rating",
                            "user_ratings_total", "url",
                        ],
                    ).get("result", {})
                except Exception as e:
                    log(f"Falha em place_details('{place_id}'): {e}", "WARN")
                    details = {}

                phone = (details.get("international_phone_number")
                         or details.get("formatted_phone_number") or "")
                results.append({
                    "nome": details.get("name") or place.get("name", ""),
                    "segmento": query.title(),
                    "categoria": categoria,
                    "endereco": details.get("formatted_address")
                                or place.get("formatted_address", ""),
                    "cidade": _extract_cidade(
                        details.get("formatted_address") or "", localizacao),
                    "telefone": phone,
                    "instagram": "",
                    "site": details.get("website") or "",
                    "rating": details.get("rating") or place.get("rating", 0),
                    "user_ratings_total": (details.get("user_ratings_total")
                                          or place.get("user_ratings_total", 0)),
                    "place_id": place_id,
                    "fonte": "google_places",
                })
                cat_count += 1

        contagem_por_categoria[categoria] = cat_count
        if cat_count < cota_alvo:
            log(f"  ⚠ {categoria}: {cat_count}/{cota_alvo} (cota não preenchida "
                f"em {localizacao})", "WARN")
        else:
            log(f"  ✓ {categoria}: {cat_count}/{cota_alvo}")

    return results


def _extract_cidade(endereco, fallback):
    """Extrai a cidade do endereço Google.
    Formato típico: 'Rua X, 123 - Bairro, Cidade - SP, CEP, Brazil'.
    Estratégia:
      1) split por vírgula → procura o item antes do ' - SP' / ' - RJ' etc.
      2) fallback: usa a cidade da busca."""
    if not endereco:
        return (fallback or "").split(",")[0].strip()
    # tenta achar 'Cidade - UF'
    import re as _re
    m = _re.search(r"([A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇa-záàâãéêíóôõúüç\s\.]+?)\s*-\s*(SP|RJ|MG|PR|RS|SC|BA|GO|DF|ES|MT|MS|PE|CE|PA|AM|RN|AL|PB|PI|MA|TO|RO|AC|RR|AP|SE)\b",
                   endereco)
    if m:
        return m.group(1).strip()
    parts = [p.strip() for p in endereco.split(",")]
    if len(parts) >= 2:
        return parts[-3].strip() if len(parts) >= 3 else parts[-2].strip()
    return (fallback or "").split(",")[0].strip()


def fetch_mock(segmentos, max_results):
    """Carrega dataset mock e filtra por segmentos opcionais."""
    mock_file = MOCK_DIR / "guarulhos_sample.json"
    if not mock_file.exists():
        log(f"Mock dataset não encontrado: {mock_file}", "ERROR")
        return []
    data = json.loads(mock_file.read_text(encoding="utf-8"))
    raw = data.get("prospects", [])
    log(f"Mock mode: carregado {len(raw)} prospects do dataset")

    if segmentos:
        wanted = [s.lower() for s in segmentos]
        raw = [
            p for p in raw
            if any(w in p.get("segmento", "").lower() for w in wanted)
        ]
        log(f"Filtro por segmentos {segmentos}: {len(raw)} restantes")

    raw = raw[:max_results]
    for p in raw:
        p.setdefault("fonte", "mock")
    return raw


def normalize(prospect):
    """Adiciona campos padrão (id, coletado_em, servico_recomendado) e garante todos os campos."""
    nome = prospect.get("nome", "")
    base = {
        "id": slugify(nome),
        "nome": nome,
        "segmento": prospect.get("segmento", ""),
        "endereco": prospect.get("endereco", ""),
        "cidade": prospect.get("cidade", ""),
        "telefone": prospect.get("telefone", ""),
        "instagram": prospect.get("instagram", ""),
        "site": prospect.get("site", ""),
        "rating": prospect.get("rating", 0),
        "user_ratings_total": prospect.get("user_ratings_total", 0),
        "place_id": prospect.get("place_id", ""),
        "fonte": prospect.get("fonte", "unknown"),
        "coletado_em": datetime.now().isoformat(timespec="seconds"),
    }
    base["servico_recomendado"] = definir_servico_recomendado(base)
    return base


def deduplicate(new_rows, existing_csv):
    """Remove prospects já presentes no CSV (por place_id ou nome+cidade)."""
    if not existing_csv.exists():
        return new_rows
    existing_ids = set()
    existing_keys = set()
    with open(existing_csv, encoding="utf-8") as f:
        import csv
        for row in csv.DictReader(f):
            if row.get("place_id"):
                existing_ids.add(row["place_id"])
            existing_keys.add((row.get("nome", "").lower(), row.get("cidade", "").lower()))
    out = []
    for r in new_rows:
        if r.get("place_id") and r["place_id"] in existing_ids:
            continue
        if (r.get("nome", "").lower(), r.get("cidade", "").lower()) in existing_keys:
            continue
        out.append(r)
    return out


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=int(env("MAX_PROSPECTS", "50")))
    parser.add_argument("--localizacao", default=None,
                        help="Force uma cidade específica (desativa rodízio)")
    parser.add_argument("--cidades", default=None,
                        help="Lista de cidades separadas por vírgula (override do rodízio)")
    parser.add_argument("--raio", type=int, default=int(env("RAIO_BUSCA_KM", "50")))
    parser.add_argument("--segmentos", default=",".join(DEFAULT_SEGMENTOS),
                        help="Lista de segmentos separados por vírgula")
    parser.add_argument("--per-segment", type=int, default=None,
                        help="Máximo de prospects por segmento (default: ilimitado)")
    parser.add_argument("--cotas-json", default=None,
                        help="Path pra JSON com cotas por categoria. Quando "
                             "presente, ignora --segmentos/--per-segment e "
                             "busca respeitando cotas. Formato: lista de "
                             '{"categoria","cota","queries":[..]}.')
    args = parser.parse_args()

    cotas_categorias = None
    if args.cotas_json:
        try:
            with open(args.cotas_json, encoding="utf-8") as f:
                cotas_categorias = json.load(f)
            log(f"Modo COTAS-CATEGORIA: {len(cotas_categorias)} categorias, "
                f"total cota = {sum(c.get('cota', 0) for c in cotas_categorias)}")
        except Exception as e:
            log(f"Falha ao ler --cotas-json={args.cotas_json}: {e}", "ERROR")
            cotas_categorias = None

    segmentos = [s.strip() for s in args.segmentos.split(",") if s.strip()]

    # Resolve quais cidades buscar
    if args.cidades:
        cidades = [c.strip() for c in args.cidades.split(",") if c.strip()]
        log(f"Cidades (override --cidades): {cidades}")
    elif args.localizacao:
        cidades = [args.localizacao]
        log(f"Cidade (override --localizacao): {args.localizacao}")
    else:
        cidades = proximas_cidades_rodizio()
        log(f"Cidades (rodízio): {cidades}")

    # Quota por cidade (distribuir max total entre cidades)
    quota_cidade = max(5, args.max // max(1, len(cidades)))

    todos = []
    if use_mock() or not env("GOOGLE_PLACES_KEY"):
        todos = fetch_mock(segmentos, args.max)
        log("Modo: MOCK (ignora rodízio)")
    elif cotas_categorias:
        log(f"Modo: REAL (Google Places) POR COTAS · raio={args.raio}km")
        for cidade in cidades:
            localizacao = cidade if "," in cidade else f"{cidade}, SP"
            log(f"→ Buscando POR COTAS em {localizacao}")
            res = fetch_real_por_cotas(cotas_categorias, localizacao, args.raio)
            for p in res:
                if not p.get("cidade"):
                    p["cidade"] = cidade
            todos.extend(res)
    else:
        log(f"Modo: REAL (Google Places) · raio={args.raio}km · quota/cidade={quota_cidade}")
        for cidade in cidades:
            localizacao = cidade if "," in cidade else f"{cidade}, SP"
            log(f"→ Buscando em {localizacao}")
            res = fetch_real(segmentos, localizacao, args.raio,
                             quota_cidade, per_segment=args.per_segment)
            for p in res:
                # garante que o campo cidade preserve a cidade real (não fallback genérico)
                if not p.get("cidade"):
                    p["cidade"] = cidade
            todos.extend(res)
            if len(todos) >= args.max:
                break

    todos = todos[:args.max]
    normalized = [normalize(p) for p in todos]

    # FILTRO: tira marcas grandes (Droga Raia, Petz, McDonald's, etc — não convertem)
    filtered = [p for p in normalized if not is_blocked_brand(p.get("nome", ""))]
    blocked_count = len(normalized) - len(filtered)
    if blocked_count > 0:
        log(f"🚫 {blocked_count} marca(s) grande(s) filtradas (blocklist)")

    new_only = deduplicate(filtered, PROSPECTS_CSV)

    log(f"Total coletados: {len(normalized)} | Após blocklist: {len(filtered)} | Novos: {len(new_only)}")
    if new_only:
        # distribuição por cidade no que foi salvo
        from collections import Counter
        dist = Counter(p.get("cidade", "?") for p in new_only)
        log(f"Distribuição por cidade: {dict(dist)}")

        append_csv(PROSPECTS_CSV, new_only, PROSPECT_FIELDS)
        log(f"✅ Salvos em {PROSPECTS_CSV}")
    else:
        log("Nenhum prospect novo pra salvar")

    print(f"PROSPECTS_NEW={len(new_only)}")
    print(f"PROSPECTS_TOTAL={len(normalized)}")
    return new_only


if __name__ == "__main__":
    main()
