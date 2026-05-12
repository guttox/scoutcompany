"""
Relatório semanal Scout — gera PDF profissional e envia no @usescout_bot.

Roda toda semana (domingo 20h via launchd). Lê data/aprendizados.json
(deve ter sido produzido por analyze_week.py imediatamente antes).

Layout:
  - Capa: logo Scout + título + período
  - Resumo executivo
  - Métricas em cards
  - Segmento campeão
  - Melhores horários
  - ICP atualizado
  - Ajustes automáticos aplicados
  - "O que o Scout vai fazer diferente na próxima semana"

Salva em ~/scout/relatorios/semana-YYYY-MM-DD.pdf e envia no Telegram.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import DATA_DIR, ROOT, env, load_env, log

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)

APRENDIZADOS_PATH = DATA_DIR / "aprendizados.json"
RELATORIOS_DIR = ROOT / "relatorios"
LOGO_PATH = ROOT / "brand" / "logo-transparent.png"

# Identidade Scout
COR_PRIMARIA = colors.HexColor("#1B2A4A")  # Obsidian Blue
COR_ACENTO = colors.HexColor("#4A90D9")    # Electric Blue
COR_TEXTO = colors.HexColor("#1A1F2E")
COR_TEXTO_CLARO = colors.HexColor("#5C6470")
COR_CARD_BG = colors.HexColor("#F4F7FB")
COR_FUNDO = colors.white


# ═══════════════════════════════════════════════════════════
# Página master (header + footer)
# ═══════════════════════════════════════════════════════════
def _page_master(canv: canvas.Canvas, doc):
    canv.saveState()
    width, height = A4
    # Faixa fina topo
    canv.setFillColor(COR_PRIMARIA)
    canv.rect(0, height - 0.4 * cm, width, 0.4 * cm, fill=True, stroke=False)
    # Rodapé
    canv.setFillColor(COR_TEXTO_CLARO)
    canv.setFont("Helvetica", 8)
    canv.drawString(2 * cm, 1.2 * cm, "Scout Intelligence Report — Confidencial")
    canv.drawRightString(width - 2 * cm, 1.2 * cm, f"Página {doc.page}")
    canv.restoreState()


def _build_styles():
    base = getSampleStyleSheet()
    return {
        "h0_capa": ParagraphStyle(
            "h0_capa", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=28, leading=34, textColor=COR_PRIMARIA, alignment=1, spaceAfter=12,
        ),
        "sub_capa": ParagraphStyle(
            "sub_capa", parent=base["Normal"], fontName="Helvetica",
            fontSize=12, leading=16, textColor=COR_TEXTO_CLARO, alignment=1, spaceAfter=24,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=18, leading=22, textColor=COR_PRIMARIA, spaceAfter=8, spaceBefore=12,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=12, leading=15, textColor=COR_ACENTO, spaceAfter=6, spaceBefore=10,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica",
            fontSize=10.5, leading=15, textColor=COR_TEXTO,
        ),
        "card_kpi": ParagraphStyle(
            "card_kpi", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=20, leading=22, textColor=COR_PRIMARIA, alignment=1,
        ),
        "card_label": ParagraphStyle(
            "card_label", parent=base["Normal"], fontName="Helvetica",
            fontSize=9, leading=12, textColor=COR_TEXTO_CLARO, alignment=1,
        ),
    }


def _capa(styles, periodo):
    el = []
    el.append(Spacer(1, 4 * cm))
    if LOGO_PATH.exists():
        try:
            img = Image(str(LOGO_PATH), width=4.5 * cm, height=4.5 * cm, kind="proportional")
            img.hAlign = "CENTER"
            el.append(img)
        except Exception:
            pass
    el.append(Spacer(1, 1.2 * cm))
    el.append(Paragraph("Relatório de Inteligência Semanal", styles["h0_capa"]))
    el.append(Paragraph(
        f"Período: {periodo['inicio']} a {periodo['fim']}",
        styles["sub_capa"]
    ))
    el.append(Spacer(1, 6 * cm))
    el.append(Paragraph(
        "Análise de prospecção, comportamento e aprendizados da operação Scout.",
        styles["sub_capa"]
    ))
    el.append(PageBreak())
    return el


def _resumo_executivo(styles, dados):
    m = dados["metricas"]
    ranking = dados.get("segmentos_ranking", [])
    icp = dados.get("icp", {})
    seg_campeao = ranking[0]["segmento"] if ranking else "—"
    taxa = m["taxa_resposta"] * 100 if m.get("taxa_resposta") else 0
    n_aj = len(dados.get("ajustes_aplicados", []))

    texto = (
        f"Esta semana o Scout fez <b>{m['disparos']} disparo(s)</b>, recebeu "
        f"<b>{m['respostas']} resposta(s)</b> (taxa {taxa:.1f}%), identificou "
        f"<b>{m['leads_quentes']} lead(s) quente(s)</b> e fechou "
        f"<b>{m['fechamentos']} contrato(s)</b>. O segmento com melhor performance "
        f"foi <b>{seg_campeao}</b>. {n_aj} ajuste(s) automático(s) foram aplicado(s) "
        f"para a próxima semana com base nos sinais coletados."
    )
    el = []
    el.append(Paragraph("Resumo Executivo", styles["h1"]))
    el.append(Paragraph(texto, styles["body"]))
    el.append(Spacer(1, 0.4 * cm))
    return el


def _cards_metricas(styles, metricas):
    el = []
    el.append(Paragraph("Métricas da Semana", styles["h1"]))

    cards = [
        (str(metricas.get("disparos", 0)), "Disparos"),
        (str(metricas.get("respostas", 0)), "Respostas"),
        (f"{(metricas.get('taxa_resposta', 0) * 100):.1f}%", "Taxa de Resposta"),
        (str(metricas.get("leads_quentes", 0)), "Leads Quentes"),
        (str(metricas.get("fechamentos", 0)), "Fechamentos"),
    ]

    # 5 cards em 1 linha — 3.4cm de largura cada
    cells = []
    for valor, label in cards:
        inner = [
            [Paragraph(valor, styles["card_kpi"])],
            [Paragraph(label, styles["card_label"])],
        ]
        t = Table(inner, colWidths=[3.0 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), COR_CARD_BG),
            ("BOX", (0, 0), (-1, -1), 0.6, COR_ACENTO),
            ("TOPPADDING", (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        cells.append(t)

    row = Table([cells], colWidths=[3.4 * cm] * 5)
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    el.append(row)
    el.append(Spacer(1, 0.4 * cm))
    return el


def _segmento_campeao(styles, ranking):
    el = []
    el.append(Paragraph("Segmento Campeão", styles["h1"]))
    if not ranking:
        el.append(Paragraph(
            "Ainda não há dados suficientes para apontar um segmento campeão. "
            "Volte na próxima semana — quando houver respostas, o Scout começa "
            "a priorizar automaticamente.",
            styles["body"]))
        return el

    data = [["Segmento", "Disparos", "Respostas", "Taxa"]]
    for r in ranking[:5]:
        data.append([
            r["segmento"],
            str(r["disparos"]),
            str(r["respostas"]),
            f"{r['taxa_resposta'] * 100:.1f}%",
        ])
    t = Table(data, colWidths=[7 * cm, 3 * cm, 3 * cm, 2.5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CARD_BG]),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    el.append(t)
    el.append(Spacer(1, 0.4 * cm))
    return el


def _melhores_horarios(styles, horarios):
    el = []
    el.append(Paragraph("Melhores Horários de Resposta", styles["h1"]))
    if not horarios:
        el.append(Paragraph(
            "Sem dados suficientes ainda. O Scout vai começar a mapear o horário "
            "em que cada segmento mais responde nas próximas semanas.",
            styles["body"]))
        return el
    linhas = []
    for hora, n in horarios.items():
        linhas.append(f"<b>{hora}</b> — {n} resposta(s)")
    el.append(Paragraph("<br/>".join(linhas), styles["body"]))
    el.append(Spacer(1, 0.4 * cm))
    return el


def _icp_atualizado(styles, icp):
    el = []
    el.append(Paragraph("Perfil de Cliente Ideal (ICP)", styles["h1"]))
    if icp.get("n_fechados_analisados", 0) == 0:
        el.append(Paragraph(
            "Sem fechamentos ainda — o ICP será construído automaticamente a cada "
            "contrato assinado. Cada cliente fechado enriquece esse perfil e o Scout "
            "prioriza prospects com características semelhantes.",
            styles["body"]))
        return el
    segs = ", ".join(icp.get("segmentos_mais_fecharam", [])) or "—"
    linhas = [
        f"<b>Fechamentos analisados:</b> {icp['n_fechados_analisados']}",
        f"<b>Segmentos que mais fecharam:</b> {segs}",
    ]
    if icp.get("score_medio_fechados") is not None:
        linhas.append(f"<b>Score médio:</b> {icp['score_medio_fechados']}")
    if icp.get("rating_medio_fechados") is not None:
        linhas.append(f"<b>Rating médio (Google):</b> {icp['rating_medio_fechados']}")
    if icp.get("pct_sem_site") is not None:
        linhas.append(f"<b>% sem site:</b> {icp['pct_sem_site']}%")
    if icp.get("pct_whatsapp") is not None:
        linhas.append(f"<b>% com WhatsApp:</b> {icp['pct_whatsapp']}%")
    el.append(Paragraph("<br/>".join(linhas), styles["body"]))
    el.append(Spacer(1, 0.4 * cm))
    return el


def _ajustes_aplicados(styles, ajustes):
    el = []
    el.append(Paragraph("Ajustes Automáticos Aplicados", styles["h1"]))
    if not ajustes:
        el.append(Paragraph(
            "Nenhum ajuste aplicado nesta semana. O Scout precisa de mais sinal "
            "(respostas e fechamentos) para começar a aprender e otimizar.",
            styles["body"]))
        return el
    linhas = []
    for i, aj in enumerate(ajustes, 1):
        linhas.append(f"<b>{i}.</b> {aj.get('descricao', '(sem descrição)')}")
    el.append(Paragraph("<br/>".join(linhas), styles["body"]))
    el.append(Spacer(1, 0.4 * cm))
    return el


def _proxima_semana(styles, dados):
    el = []
    el.append(Paragraph("O que o Scout vai fazer diferente", styles["h1"]))
    ranking = dados.get("segmentos_ranking", [])
    ajustes = dados.get("ajustes_aplicados", [])

    bullets = []
    if ranking:
        top = ranking[0]
        bullets.append(
            f"Aumentar prospecção em <b>{top['segmento']}</b> "
            f"(taxa de resposta {top['taxa_resposta'] * 100:.1f}% nesta semana)."
        )
    if dados.get("icp", {}).get("n_fechados_analisados", 0) > 0:
        bullets.append(
            "Aplicar boost de score em prospects com perfil similar aos clientes já fechados."
        )
    if not ajustes:
        bullets.append(
            "Coletar mais sinal nesta próxima semana — manter os disparos consistentes "
            "para começar a identificar padrões."
        )
    if dados.get("metricas", {}).get("taxa_resposta", 0) < 0.05:
        bullets.append(
            "Taxa de resposta abaixo de 5% — revisar mensagens e horários "
            "(considerar ajuste no prompt)."
        )

    el.append(Paragraph("<br/>".join(f"• {b}" for b in bullets), styles["body"]))
    el.append(Spacer(1, 0.4 * cm))
    return el


# ═══════════════════════════════════════════════════════════
# Telegram
# ═══════════════════════════════════════════════════════════
def _telegram_send_document(pdf_path, caption):
    token = env("TELEGRAM_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("Telegram não configurado — pulando envio do PDF", "WARN")
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    # multipart manual usando urllib (sem dependência extra)
    boundary = "----ScoutBoundary7K"
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    parts = []
    def add_field(name, value):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n")
    add_field("chat_id", chat_id)
    add_field("caption", caption)
    add_field("parse_mode", "HTML")
    parts_pre = "".join(parts).encode("utf-8")
    fn = os.path.basename(pdf_path)
    file_header = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"document\"; filename=\"{fn}\"\r\n"
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8")
    file_footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = parts_pre + file_header + pdf_bytes + file_footer
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                log(f"Telegram sendDocument http={resp.status}", "WARN")
            return ok
    except Exception as e:
        log(f"Telegram sendDocument exception: {e}", "ERROR")
        return False


def _telegram_send_text(text):
    token = env("TELEGRAM_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=payload, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log(f"Telegram sendMessage exception: {e}", "ERROR")
        return False


def montar_resumo_telegram(dados, num_semana):
    m = dados["metricas"]
    ranking = dados.get("segmentos_ranking", [])
    icp = dados.get("icp", {})
    seg_top = ranking[0]["segmento"] if ranking else "—"

    destaques = []
    if m.get("fechamentos"):
        destaques.append(f"💰 {m['fechamentos']} fechamento(s) na semana")
    if m.get("leads_quentes"):
        destaques.append(f"🔥 {m['leads_quentes']} lead(s) quente(s) detectado(s)")
    if ranking and ranking[0]["taxa_resposta"] > 0:
        destaques.append(
            f"📈 Segmento campeão: <b>{seg_top}</b> "
            f"({ranking[0]['taxa_resposta'] * 100:.0f}% resposta)"
        )
    if not destaques:
        destaques.append("📊 Operação coletando sinal — sem destaques ainda")

    return (
        f"📊 <b>Relatório Scout — Semana {num_semana}</b>\n"
        f"Confira os insights e os ajustes automáticos aplicados.\n\n"
        + "\n".join(f"• {d}" for d in destaques[:3])
    )


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
def main():
    load_env()
    log("═══════════════════════════")
    log("WEEKLY REPORT — INICIANDO")
    log("═══════════════════════════")

    if not APRENDIZADOS_PATH.exists():
        log(f"aprendizados.json não existe em {APRENDIZADOS_PATH} — "
            f"rode analyze_week.py antes", "ERROR")
        return
    dados = json.loads(APRENDIZADOS_PATH.read_text(encoding="utf-8"))

    RELATORIOS_DIR.mkdir(parents=True, exist_ok=True)
    hoje = datetime.now().date().isoformat()
    pdf_path = RELATORIOS_DIR / f"semana-{hoje}.pdf"

    # Calcula número da semana (offset desde primeiro_disparo, fallback semana 1)
    try:
        from _common import calcular_semana_atual
        num_semana = calcular_semana_atual()
    except Exception:
        num_semana = 1

    styles = _build_styles()

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title="Relatório de Inteligência Semanal — Scout",
        author="Scout Company",
    )

    story = []
    story += _capa(styles, dados.get("periodo", {"inicio": "—", "fim": "—"}))
    story += _resumo_executivo(styles, dados)
    story += _cards_metricas(styles, dados["metricas"])
    story += _segmento_campeao(styles, dados.get("segmentos_ranking", []))
    story += _melhores_horarios(styles, dados.get("melhores_horarios", {}))
    story += _icp_atualizado(styles, dados.get("icp", {}))
    story += _ajustes_aplicados(styles, dados.get("ajustes_aplicados", []))
    story += _proxima_semana(styles, dados)

    doc.build(story, onFirstPage=_page_master, onLaterPages=_page_master)
    log(f"✓ PDF salvo: {pdf_path} ({pdf_path.stat().st_size // 1024}KB)")

    # Envia no Telegram
    resumo = montar_resumo_telegram(dados, num_semana)
    ok_doc = _telegram_send_document(
        pdf_path,
        caption=f"📊 Relatório Scout — Semana {num_semana}\nConfira os insights e os ajustes automáticos aplicados.",
    )
    if ok_doc:
        log("✓ PDF enviado no Telegram")
    else:
        log("✗ Falha ao enviar PDF — confira TELEGRAM_TOKEN/CHAT_ID", "WARN")
    ok_msg = _telegram_send_text(resumo)
    if ok_msg:
        log("✓ Resumo de destaques enviado no Telegram")
    else:
        log("✗ Falha no envio do resumo", "WARN")

    return pdf_path


if __name__ == "__main__":
    main()
