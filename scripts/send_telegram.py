"""
Etapa 4 — envia relatório diário no Telegram.
Lê qualificados.csv + mensagens/ e formata o digest.

Uso:
  python3 send_telegram.py             # envia relatório do dia
  python3 send_telegram.py --top 10    # limita a top 10 prospects
  python3 send_telegram.py --dry-run   # imprime sem enviar
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    MENS_DIR, QUALIFICADOS_CSV, env, load_env, log, read_csv, slugify,
)

TELEGRAM_MAX_LEN = 4096
RETRY_ATTEMPTS = 3
RETRY_DELAYS = [1, 3, 5]  # segundos entre tentativas


def telegram_send(text, parse_mode=None, reply_markup=None):
    """Envia mensagem com retry automático em falha (SSL, timeout, rede)."""
    token = env("TELEGRAM_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("TELEGRAM_TOKEN/CHAT_ID ausentes", "ERROR")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    data = urllib.parse.urlencode(payload).encode("utf-8")

    last_err = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, data=data, timeout=20) as resp:
                if resp.status == 200:
                    return True
                last_err = f"HTTP {resp.status}"
        except Exception as e:
            last_err = str(e)
        if attempt < RETRY_ATTEMPTS - 1:
            wait = RETRY_DELAYS[attempt]
            log(f"Falha telegram (tentativa {attempt+1}/{RETRY_ATTEMPTS}): {last_err} — retry em {wait}s", "WARN")
            time.sleep(wait)
    log(f"Falha telegram final após {RETRY_ATTEMPTS} tentativas: {last_err}", "ERROR")
    return False


def _build_action_keyboard(prospect_id):
    """Inline keyboard com botões pra atualizar pipeline direto do Telegram.
    Callback format: <acao>:<prospect_id>"""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Abordado",  "callback_data": f"abordado:{prospect_id}"},
                {"text": "🤝 Reunião",  "callback_data": f"reuniao:{prospect_id}"},
            ],
            [
                {"text": "💰 Fechado",   "callback_data": f"fechado:{prospect_id}"},
                {"text": "❌ Perdido",   "callback_data": f"perdido:{prospect_id}"},
            ],
        ]
    }


def html_escape(s):
    """Escape mínimo para parse_mode=HTML do Telegram."""
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def chunk_text(text, limit=TELEGRAM_MAX_LEN - 200):
    """Quebra texto longo em pedaços que cabem no Telegram, preservando blocos."""
    chunks = []
    cur = []
    cur_len = 0
    for block in text.split("\n\n"):
        block_len = len(block) + 2
        if cur_len + block_len > limit and cur:
            chunks.append("\n\n".join(cur))
            cur = [block]
            cur_len = block_len
        else:
            cur.append(block)
            cur_len += block_len
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _carregar_secoes(prospect):
    """Lê o arquivo .txt e retorna (whatsapp, email_assunto, email_corpo)."""
    slug = slugify(prospect.get("nome", "") or prospect.get("id", ""))
    path = MENS_DIR / f"{slug}.txt"
    if not path.exists():
        return "", "", ""
    content = path.read_text(encoding="utf-8")

    def extrair(label, ate=None):
        """Extrai conteúdo entre o cabeçalho do label e o próximo bloco."""
        if label not in content:
            return ""
        after = content.split(label, 1)[1]
        lines = after.splitlines()
        body, started = [], False
        for line in lines:
            if line.startswith("═"):
                if started:
                    break
                started = True
                continue
            if started:
                if ate and line.startswith(ate):
                    break
                body.append(line)
        return "\n".join(body).strip()

    whatsapp = extrair("WHATSAPP", ate="EMAIL")
    email_assunto = extrair("EMAIL — Assunto:", ate="═══")
    email_corpo = extrair("EMAIL — Corpo:")
    return whatsapp, email_assunto, email_corpo


def carregar_mensagem_whatsapp(prospect):
    """Compat: retorna apenas o WhatsApp."""
    wa, _, _ = _carregar_secoes(prospect)
    return wa


def make_wa_link(wa_link, message):
    """Toma 'https://wa.me/55XXX' e adiciona ?text=URL_ENCODED_MESSAGE."""
    if not wa_link or not message:
        return wa_link
    sep = "&" if "?" in wa_link else "?"
    return f"{wa_link}{sep}text={urllib.parse.quote(message)}"


def make_mailto_link(email, subject, body):
    """Constrói mailto:email?subject=...&body=... URL-encoded."""
    if not email:
        return ""
    qs = urllib.parse.urlencode({"subject": subject or "", "body": body or ""})
    return f"mailto:{email}?{qs}"


def situacao_label(situacao):
    s = (situacao or "").lower()
    if "sem site" in s and "instagram" not in s:
        return "Sem site"
    if "só instagram" in s or ("instagram" in s and "sem site" in s):
        return "Só Instagram"
    if "desatualizado" in s or "antigo" in s:
        return "Site desatualizado"
    return "Outro"


PRIORIDADE_LABEL = {
    "1": "P1 · WhatsApp",
    "2": "P2 · Email",
    "3": "P3 · Telefone",
    "4": "P4 · Sem canal verificado",
}


def _tel_clean(tel):
    """Mantém só dígitos e + pra usar em tel: links."""
    return ''.join(c for c in (tel or '') if c.isdigit() or c == '+')


def botoes_acao_html(prospect, whatsapp_msg, email_assunto, email_corpo):
    """Botões de ação clicáveis com mensagem pré-carregada.

    Telegram não tem 'botões' fora de inline keyboards, mas links HTML
    aparecem como links sublinhados clicáveis — funcionam tipo botão.
    """
    tem_wa = prospect.get("tem_whatsapp", "")
    wa_link_base = prospect.get("whatsapp_link", "")
    tel = prospect.get("telefone", "")
    email = prospect.get("email", "")
    tem_email = prospect.get("tem_email", "")

    botoes = []

    # Botão WhatsApp — abre WA com mensagem JÁ DIGITADA
    if tem_wa == "sim" and wa_link_base:
        wa_full = make_wa_link(wa_link_base, whatsapp_msg)
        botoes.append(
            f'📲 <a href="{html_escape(wa_full)}"><b>ABRIR NO WHATSAPP COM MENSAGEM PRONTA</b></a>'
        )
    elif tem_wa == "nao_verificado" and tel:
        # Mesmo sem confirmar WA, podemos tentar — wa.me abre mesmo se não tiver WA
        from enrich_contacts import normalize_phone_br
        canonical, tipo = normalize_phone_br(tel)
        if canonical:
            wa_link_base = f"https://wa.me/{canonical}"
            wa_full = make_wa_link(wa_link_base, whatsapp_msg)
            botoes.append(
                f'📲 <a href="{html_escape(wa_full)}">Tentar WhatsApp (fixo, pode não responder)</a>'
            )

    # Botão Email — abre cliente de email com tudo preenchido + mostra assunto
    if tem_email == "sim" and email:
        mailto = make_mailto_link(email, email_assunto, email_corpo)
        botoes.append(
            f'📧 <a href="{html_escape(mailto)}"><b>ENVIAR EMAIL ({html_escape(email)})</b></a>'
        )
        if email_assunto:
            botoes.append(
                f'    📋 <i>Assunto:</i> "{html_escape(email_assunto)}"'
            )

    # Telefone — sempre exibe quando houver, como fallback rápido
    # (especialmente útil quando email é a prioridade — o prospect pode preferir ligar)
    if tel:
        tel_link = _tel_clean(tel)
        botoes.append(
            f'📞 <a href="tel:{tel_link}">Ligar: {html_escape(tel)}</a>'
        )

    return "\n".join(botoes) if botoes else "<i>Sem canal verificado — só Instagram ou ligação manual</i>"


def _sort_qualificados(qualificados, top_n):
    """Ordena por prioridade asc, depois score desc; retorna top_n."""
    def sort_key(p):
        prio = int(p.get("prioridade") or "4")
        score = -int(float(p.get("score") or 0))
        return (prio, score)
    return sorted(qualificados, key=sort_key)[:top_n]


def montar_resumo_header(selecionados, today):
    """Header de abertura — 1 msg curta com totais por canal."""
    if not selecionados:
        return None

    counts = {"wa": 0, "email": 0, "tel_only": 0, "sem_canal": 0}
    for p in selecionados:
        wa = p.get("tem_whatsapp") == "sim"
        em = p.get("tem_email") == "sim"
        tel = bool((p.get("telefone") or "").strip())
        if wa:
            counts["wa"] += 1
        elif em:
            counts["email"] += 1
        elif tel:
            counts["tel_only"] += 1
        else:
            counts["sem_canal"] += 1

    return (
        f"🔍 <b>Scout — Prospecção do dia</b>\n"
        f"📅 {html_escape(today)}\n\n"
        f"🎯 <b>{len(selecionados)} prospects qualificados</b>\n"
        f"📱 {counts['wa']} com WhatsApp · "
        f"📧 {counts['email']} com email · "
        f"📞 {counts['tel_only']} só telefone"
        + (f" · ⚠️ {counts['sem_canal']} sem canal" if counts['sem_canal'] else "")
        + "\n\n"
        f"👇 Cada prospect chega em uma mensagem separada com botões pra "
        f"<b>abrir WhatsApp/Email já com texto pronto</b> e botões pra "
        f"<b>marcar status</b> (Abordado / Reunião / Fechado / Perdido)."
    )


def montar_card_prospect(p, idx, total):
    """Monta o card de UM prospect (1 msg Telegram)."""
    nome = html_escape(p.get("nome", ""))
    segmento = html_escape(p.get("segmento", ""))
    cidade = html_escape(p.get("cidade", ""))
    situacao = html_escape(situacao_label(p.get("situacao", "")))
    score = html_escape(p.get("score", ""))

    whatsapp_msg, email_assunto, email_corpo = _carregar_secoes(p)
    if not whatsapp_msg:
        whatsapp_msg = "(mensagem não disponível — rode generate_messages.py)"

    botoes = botoes_acao_html(p, whatsapp_msg, email_assunto, email_corpo)
    whatsapp_preview = html_escape(whatsapp_msg)

    return f"""<b>{idx}/{total}</b> · <b>{nome}</b>
🏷️ {segmento}  ·  📍 {cidade}
🌐 {situacao}  ·  ⭐ {score}/10

<b>👉 AÇÕES</b>
{botoes}

<b>💬 Mensagem pronta</b>:
<blockquote expandable>{whatsapp_preview}</blockquote>"""


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=15, help="Quantos prospects enviar")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-buttons", action="store_true",
                        help="Desabilita inline keyboard de status (Abordado/etc)")
    args = parser.parse_args()

    qualificados = read_csv(QUALIFICADOS_CSV)
    log(f"Carregados {len(qualificados)} qualificados")

    selecionados = _sort_qualificados(qualificados, args.top)
    today = datetime.now().strftime("%d/%m/%Y")

    if args.dry_run:
        header = montar_resumo_header(selecionados, today)
        print("=== HEADER ===")
        print(header)
        for i, p in enumerate(selecionados, 1):
            print(f"\n=== CARD {i}/{len(selecionados)} ===")
            print(montar_card_prospect(p, i, len(selecionados)))
        return

    # Envia o header (resumo)
    header = montar_resumo_header(selecionados, today)
    if header:
        if telegram_send(header, parse_mode="HTML"):
            log(f"✅ Resumo enviado ({len(selecionados)} prospects)")
        else:
            log("❌ Falha ao enviar resumo — abortando envio dos cards", "ERROR")
            return

    # Envia 1 mensagem por prospect com inline keyboard
    sent = 0
    for i, p in enumerate(selecionados, 1):
        card = montar_card_prospect(p, i, len(selecionados))
        keyboard = None if args.no_buttons else _build_action_keyboard(p.get("id", ""))
        if telegram_send(card, parse_mode="HTML", reply_markup=keyboard):
            sent += 1
            log(f"  ✅ {i}/{len(selecionados)} — {p.get('nome', '?')}")
        else:
            log(f"  ❌ Falha enviando prospect {i}: {p.get('nome', '?')}", "ERROR")
        # Pequeno delay pra evitar rate limit do Telegram (30 msg/segundo é o limite)
        time.sleep(0.3)

    log(f"📊 Envio finalizado: {sent}/{len(selecionados)} prospects enviados como mensagens individuais")


if __name__ == "__main__":
    main()
