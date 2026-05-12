"""
Bot Telegram interativo do Scout (@usescout_bot).

Responde a:
  /start         → mensagem de boas-vindas
  /contato       → link wa.me + site
  qualquer outra → mensagem fallback com site

Hoje NÃO está rodando automaticamente. Pra ativar:
  python3 ~/scout/scripts/telegram_responder.py          # roda em foreground
  nohup python3 ~/scout/scripts/telegram_responder.py &  # roda em background

⚠️ Mantém UMA instância só rodando (long-polling não pode ter 2 ao mesmo tempo
   sem getUpdates conflict). Pra usar como serviço, considerar launchd plist.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    PIPELINE_CSV, PIPELINE_FIELDS, env, load_env, log, read_csv, write_csv,
)

# Mapeamento de callback → status do pipeline
CALLBACK_TO_STATUS = {
    "abordado": "Abordado",
    "reuniao":  "Reunião",
    "fechado":  "Fechado",
    "perdido":  "Perdido",
}
CALLBACK_TO_EMOJI = {
    "abordado": "✅",
    "reuniao":  "🤝",
    "fechado":  "💰",
    "perdido":  "❌",
}

SITE = "scoutcompany.com.br"
# Lê de WHATSAPP_SCOUT no .env, fallback pro padrão
load_env()  # garante que vars já estão carregadas antes do dict abaixo
WPP_NUM = env("WHATSAPP_SCOUT", "5511940670464")
WPP_LINK = f"https://wa.me/{WPP_NUM}?text=Ol%C3%A1!%20Quero%20saber%20mais%20sobre%20o%20Scout."

# ─────────────────────────────────────────────
# MENSAGENS
# ─────────────────────────────────────────────
MSG_START = (
    "Olá! Aqui é a Scout — tecnologia que vende.\n"
    "Sites, sistemas e automação pra negócios que querem crescer.\n\n"
    f"🌐 Conheça nosso trabalho: {SITE}\n"
    "💬 Pra falar com a equipe, use /contato"
)

MSG_CONTATO = (
    "Pra iniciar um projeto com a Scout:\n\n"
    f"🌐 Site: {SITE}\n"
    f"📱 WhatsApp: {WPP_LINK}"
)

MSG_FALLBACK = (
    "Não entendi sua mensagem.\n\n"
    f"Conheça mais sobre a Scout em {SITE} ou fale com a equipe pelo /contato."
)


# ─────────────────────────────────────────────
# Telegram API helpers
# ─────────────────────────────────────────────
def api_url(method):
    token = env("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN ausente no .env")
    return f"https://api.telegram.org/bot{token}/{method}"


def get_updates(offset=None, timeout=30):
    """Long-polling — espera até `timeout`s por novos updates.
    allowed_updates explícito pra receber mensagens E cliques de inline keyboard."""
    params = {
        "timeout": timeout,
        "allowed_updates": json.dumps(["message", "edited_message", "callback_query"]),
    }
    if offset is not None:
        params["offset"] = offset
    url = api_url("getUpdates") + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=timeout + 5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f"getUpdates falhou: {e}", "WARN")
        return None


def send_message(chat_id, text, parse_mode=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "false",
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    data = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(api_url("sendMessage"), data=data, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"sendMessage falhou: {e}", "ERROR")
        return False


def answer_callback(callback_query_id, text="", show_alert=False):
    """Confirma o clique no botão (some o spinner do Telegram)."""
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": "true" if show_alert else "false",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(api_url("answerCallbackQuery"), data=data, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"answerCallbackQuery falhou: {e}", "ERROR")
        return False


def edit_message_reply_markup(chat_id, message_id, reply_markup=None):
    """Edita o teclado da mensagem (pra mostrar 'status atualizado')."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": json.dumps(reply_markup or {}),
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(api_url("editMessageReplyMarkup"), data=data, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


# ─────────────────────────────────────────────
# Pipeline updater (do clique → CSV)
# ─────────────────────────────────────────────
def atualizar_pipeline(prospect_id, novo_status):
    """Atualiza status do prospect no pipeline.csv. Retorna o nome ou None."""
    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        return None
    found_nome = None
    for row in pipeline:
        if row.get("id") == prospect_id:
            row["status"] = novo_status
            if novo_status == "Abordado" and not row.get("data_abordagem"):
                row["data_abordagem"] = datetime.now().strftime("%Y-%m-%d")
            if novo_status in ("Abordado", "Reunião", "Fechado") and not row.get("data_envio_site"):
                row["data_envio_site"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            found_nome = row.get("nome", prospect_id)
            break
    if found_nome:
        write_csv(PIPELINE_CSV, pipeline, PIPELINE_FIELDS)
    return found_nome


# ─────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────
def handle_callback(cb):
    """Processa um inline keyboard click."""
    cb_id = cb.get("id")
    data = (cb.get("data") or "").strip()
    msg = cb.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")

    if not data or ":" not in data:
        answer_callback(cb_id, "Callback inválido")
        return

    acao, _, prospect_id = data.partition(":")
    novo_status = CALLBACK_TO_STATUS.get(acao)
    emoji = CALLBACK_TO_EMOJI.get(acao, "•")

    if not novo_status:
        answer_callback(cb_id, "Ação não reconhecida")
        return

    nome = atualizar_pipeline(prospect_id, novo_status)
    if nome:
        # Remove o teclado (não pode mais clicar) e mostra confirmação visual
        edit_message_reply_markup(chat_id, message_id, {
            "inline_keyboard": [[
                {"text": f"{emoji} Marcado como {novo_status}", "callback_data": "noop"}
            ]]
        })
        answer_callback(cb_id, f"{emoji} {nome} → {novo_status}")
        log(f"→ Pipeline atualizada: {prospect_id} → {novo_status}")
    else:
        answer_callback(cb_id, f"⚠️ Prospect {prospect_id} não está na pipeline", show_alert=True)
        log(f"⚠️ Prospect {prospect_id} não encontrado", "WARN")


def handle_update(update):
    # 1. Callback de inline keyboard
    if "callback_query" in update:
        handle_callback(update["callback_query"])
        return

    # 2. Mensagem comum
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id:
        return

    if text.startswith("/start"):
        send_message(chat_id, MSG_START)
        log(f"→ /start respondido pra chat {chat_id}")
    elif text.startswith("/contato"):
        send_message(chat_id, MSG_CONTATO)
        log(f"→ /contato respondido pra chat {chat_id}")
    else:
        send_message(chat_id, MSG_FALLBACK)
        log(f"→ fallback enviado pra chat {chat_id} (texto: {text[:40]!r})")


def main():
    load_env()
    log("═══════════════════════════")
    log("Scout Responder — INICIADO")
    log("═══════════════════════════")
    log(f"Site: {SITE} · WhatsApp: {WPP_NUM}")
    last_update_id = None
    while True:
        try:
            data = get_updates(offset=last_update_id, timeout=30)
            if not data or not data.get("ok"):
                time.sleep(3)
                continue
            for update in data.get("result", []):
                handle_update(update)
                last_update_id = update["update_id"] + 1
        except KeyboardInterrupt:
            log("Encerrado por usuário.")
            break
        except Exception as e:
            log(f"Loop error: {e}", "ERROR")
            time.sleep(5)


if __name__ == "__main__":
    main()
