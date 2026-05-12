"""
Aprendizado contínuo do Scout. Roda toda semana (domingo 20h via launchd).

Analisa últimos 7 dias e gera ~/scout/data/aprendizados.json com:
  - segmentos_ranking: taxa de resposta por segmento
  - melhores_horarios_por_segmento: horas com mais resposta
  - icp: perfil de cliente ideal (calculado dos fechamentos)
  - ajustes_aplicados: lista de regras que vão influenciar qualify/dispatcher

Esse arquivo é lido por qualify.py pra dar boost de score em prospects similares
ao ICP / em segmentos campeões.
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    CONVERSAS_DIR, DATA_DIR, PIPELINE_CSV, QUALIFICADOS_CSV,
    load_env, log, read_csv, read_fila,
)

APRENDIZADOS_PATH = DATA_DIR / "aprendizados.json"
JANELA_DIAS = 7


def _digits(s):
    return "".join(c for c in str(s or "") if c.isdigit())


def _normalize_segmento(s):
    return (s or "").strip().title()


def _coletar_periodo():
    hoje = datetime.now()
    inicio = hoje - timedelta(days=JANELA_DIAS)
    return inicio, hoje


def _analisar_segmentos(inicio, fim, pipeline, qualificados, fila):
    """Retorna lista ordenada de segmentos com taxa de resposta.

    Disparos contados pela fila (items com enviado_em/dryrun no período).
    Respostas contadas pelas conversas (msg role=user dentro do período)
    cruzando com o pipeline.csv pelo número.
    """
    seg_por_id = {q["id"]: _normalize_segmento(q.get("segmento")) for q in qualificados}
    seg_por_numero = {}
    for p in pipeline:
        seg = _normalize_segmento(p.get("segmento"))
        contato = _digits(p.get("contato"))
        if contato:
            seg_por_numero[contato] = seg

    # Disparos por segmento
    disparos = Counter()
    for it in fila.get("items", []):
        ts = it.get("enviado_em") or it.get("falhou_em")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts)
        except Exception:
            continue
        if not (inicio <= t <= fim):
            continue
        seg = seg_por_id.get(it.get("id")) or _normalize_segmento(it.get("segmento"))
        disparos[seg] += 1

    # Respostas por segmento — conta números que receberam msg do prospect no período
    respostas = Counter()
    if CONVERSAS_DIR.exists():
        for f in CONVERSAS_DIR.glob("*.json"):
            try:
                conv = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            numero = _digits(conv.get("numero") or f.stem)
            seg = None
            # tenta achar segmento via pipeline pelo número
            for k, v in seg_por_numero.items():
                if k and (k in numero or numero in k):
                    seg = v
                    break
            if not seg:
                continue
            # se tem ao menos 1 msg do user no período → contou como resposta
            tem_resposta = False
            for m in conv.get("mensagens", []):
                if m.get("role") != "user":
                    continue
                try:
                    mt = datetime.fromisoformat(m.get("ts", ""))
                except Exception:
                    continue
                if inicio <= mt <= fim:
                    tem_resposta = True
                    break
            if tem_resposta:
                respostas[seg] += 1

    ranking = []
    for seg, n_disp in disparos.items():
        n_resp = respostas.get(seg, 0)
        taxa = (n_resp / n_disp) if n_disp else 0.0
        ranking.append({
            "segmento": seg,
            "disparos": n_disp,
            "respostas": n_resp,
            "taxa_resposta": round(taxa, 3),
        })
    ranking.sort(key=lambda x: (-x["taxa_resposta"], -x["disparos"]))
    return ranking


def _analisar_horarios(inicio, fim):
    """Histograma de horários (HH) em que prospects responderam dentro da janela."""
    bucket = defaultdict(int)
    if not CONVERSAS_DIR.exists():
        return {}
    for f in CONVERSAS_DIR.glob("*.json"):
        try:
            conv = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in conv.get("mensagens", []):
            if m.get("role") != "user":
                continue
            try:
                t = datetime.fromisoformat(m.get("ts", ""))
            except Exception:
                continue
            if inicio <= t <= fim:
                bucket[t.hour] += 1
    if not bucket:
        return {}
    top = sorted(bucket.items(), key=lambda kv: -kv[1])[:3]
    return {f"{h:02d}h": n for h, n in top}


def _construir_icp(pipeline, qualificados):
    """Constrói o perfil de cliente ideal a partir dos fechamentos histórico.

    Cada fechamento contribui: segmento, score, rating, tem_site, tem_whatsapp.
    Retorna dict com agregados (médias e contagens).
    """
    qmap = {q["id"]: q for q in qualificados}
    fechados = [p for p in pipeline if p.get("status", "").lower() == "fechado"]
    icp = {
        "n_fechados_analisados": len(fechados),
        "segmentos_mais_fecharam": [],
        "score_medio_fechados": None,
        "rating_medio_fechados": None,
        "pct_sem_site": None,
        "pct_whatsapp": None,
    }
    if not fechados:
        return icp

    segs = Counter()
    scores, ratings = [], []
    sem_site, com_wpp = 0, 0
    for p in fechados:
        q = qmap.get(p.get("id"))
        seg = _normalize_segmento(p.get("segmento") or (q.get("segmento") if q else ""))
        if seg:
            segs[seg] += 1
        if q:
            try:
                scores.append(float(q.get("score") or 0))
            except Exception:
                pass
            try:
                r = float(q.get("rating") or 0)
                if r:
                    ratings.append(r)
            except Exception:
                pass
            if not (q.get("site") or "").strip():
                sem_site += 1
            if (q.get("tem_whatsapp") or "").strip().lower() == "sim":
                com_wpp += 1

    icp["segmentos_mais_fecharam"] = [s for s, _ in segs.most_common(3)]
    if scores:
        icp["score_medio_fechados"] = round(mean(scores), 2)
    if ratings:
        icp["rating_medio_fechados"] = round(mean(ratings), 2)
    icp["pct_sem_site"] = round(100.0 * sem_site / len(fechados), 1)
    icp["pct_whatsapp"] = round(100.0 * com_wpp / len(fechados), 1)
    return icp


def _gerar_ajustes(ranking, icp):
    aj = []
    # Priorizar top 3 segmentos por taxa de resposta
    top_seg = [r["segmento"] for r in ranking[:3] if r["taxa_resposta"] > 0]
    if top_seg:
        aj.append({
            "tipo": "priorizar_segmentos",
            "segmentos": top_seg,
            "score_boost": 1,
            "descricao": f"Top 3 segmentos por taxa de resposta: {', '.join(top_seg)}",
        })
    # Bonus ICP
    if icp.get("n_fechados_analisados", 0) > 0 and icp.get("segmentos_mais_fecharam"):
        aj.append({
            "tipo": "icp_match",
            "segmentos_icp": icp["segmentos_mais_fecharam"],
            "rating_min": icp.get("rating_medio_fechados"),
            "score_boost": 2,
            "descricao": (
                f"Prospects com segmento similar aos fechados "
                f"({', '.join(icp['segmentos_mais_fecharam'])}) ganham +2 no score"
            ),
        })
    return aj


def main():
    load_env()
    log("═══════════════════════════")
    log("ANALYZE WEEK — INICIANDO")
    log("═══════════════════════════")
    inicio, fim = _coletar_periodo()
    pipeline = read_csv(PIPELINE_CSV)
    qualificados = read_csv(QUALIFICADOS_CSV)
    fila = read_fila()

    ranking = _analisar_segmentos(inicio, fim, pipeline, qualificados, fila)
    horarios = _analisar_horarios(inicio, fim)
    icp = _construir_icp(pipeline, qualificados)
    ajustes = _gerar_ajustes(ranking, icp)

    # Métricas top
    total_disp = sum(r["disparos"] for r in ranking)
    total_resp = sum(r["respostas"] for r in ranking)
    leads_quentes = 0
    if CONVERSAS_DIR.exists():
        for f in CONVERSAS_DIR.glob("*.json"):
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
                if c.get("lead_quente"):
                    leads_quentes += 1
            except Exception:
                pass
    fechamentos = sum(1 for p in pipeline if p.get("status", "").lower() == "fechado")

    aprend = {
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        "periodo": {
            "inicio": inicio.date().isoformat(),
            "fim": fim.date().isoformat(),
        },
        "metricas": {
            "disparos": total_disp,
            "respostas": total_resp,
            "taxa_resposta": round(total_resp / total_disp, 3) if total_disp else 0,
            "leads_quentes": leads_quentes,
            "fechamentos": fechamentos,
        },
        "segmentos_ranking": ranking,
        "melhores_horarios": horarios,
        "icp": icp,
        "ajustes_aplicados": ajustes,
    }

    APRENDIZADOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    APRENDIZADOS_PATH.write_text(
        json.dumps(aprend, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"✓ Aprendizados salvos em {APRENDIZADOS_PATH}")
    log(f"  Disparos {total_disp} · Respostas {total_resp} · Leads quentes {leads_quentes} "
        f"· Fechamentos {fechamentos}")
    log(f"  Segmentos analisados: {len(ranking)} · Ajustes ativos: {len(ajustes)}")
    return aprend


if __name__ == "__main__":
    main()
