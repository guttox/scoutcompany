"""
Servidor Flask que recebe webhooks da Evolution API e dispara o agente IA.

Roda em http://0.0.0.0:5005 por padrão (5000 conflita com AirPlay no macOS).

Endpoints:
  POST /webhook/whatsapp   — Evolution chama aqui (MESSAGES_UPSERT)
  GET  /health             — sanidade

Como a Evolution roda dentro do Docker, registre o webhook apontando para
http://host.docker.internal:5005/webhook/whatsapp (não localhost).

Uso:
  ./venv/bin/python scripts/webhook_server.py
"""
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch_dry_run, env, load_env, log

load_env()

from flask import Flask, jsonify, request

import whatsapp_responder

app = Flask(__name__)
PORT = int(env("WEBHOOK_PORT", "5005"))


def _extrai_numero(remote_jid):
    """Pega só os dígitos antes do @ no jid (ex '5511940670464@s.whatsapp.net')."""
    if not remote_jid:
        return ""
    base = remote_jid.split("@", 1)[0]
    return "".join(c for c in base if c.isdigit())


def _extrai_texto(payload_message):
    """Extrai texto plain de várias formas possíveis (Baileys variations)."""
    if not payload_message:
        return ""
    # Texto simples
    if "conversation" in payload_message:
        return payload_message["conversation"]
    # Texto formatado / extendedTextMessage
    ext = payload_message.get("extendedTextMessage") or {}
    if ext.get("text"):
        return ext["text"]
    # Caption em mídia (image/video) opcional
    for k in ("imageMessage", "videoMessage", "documentMessage"):
        m = payload_message.get(k) or {}
        if m.get("caption"):
            return m["caption"]
    # Botões e listas — ignora (texto vazio)
    return ""


def processar_async(numero, texto, push_name):
    """Roda o responder em thread separada — webhook responde imediatamente
    pra não bloquear a Evolution API."""
    try:
        out = whatsapp_responder.responder_mensagem(numero, texto, nome_pushname=push_name)
        log(f"webhook → responder OK: {out}")
    except Exception as e:
        log(f"erro no responder: {e}", "ERROR")


@app.route("/health", methods=["GET"])
def health():
    # Re-resolve dispatch mode em cada chamada (pra refletir mudanças no .env sem restart)
    load_env()
    dry = dispatch_dry_run()
    return jsonify({
        "ok": True,
        "service": "scout-webhook",
        "port": PORT,
        "dispatch_mode": (env("DISPATCH_MODE", "") or "").upper() or "(unset)",
        "dry_run": dry,
        "modo_envio": "LIVE — envia WhatsApp real" if not dry else "DRY — só simula",
    })


@app.route("/webhook/whatsapp", methods=["POST", "GET"])
def webhook_whatsapp():
    if request.method == "GET":
        return jsonify({"ok": True, "msg": "use POST"})
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}

    event = payload.get("event") or payload.get("type") or ""
    data = payload.get("data") or {}

    # Eventos que não nos interessam — só MESSAGES_UPSERT
    if event and event.lower() not in ("messages.upsert", "messages_upsert"):
        return jsonify({"ok": True, "ignored": True, "reason": f"event={event}"})

    key = data.get("key") or {}
    remote_jid = key.get("remoteJid") or ""
    from_me = bool(key.get("fromMe"))
    is_group = "@g.us" in remote_jid

    if from_me:
        return jsonify({"ok": True, "ignored": True, "reason": "fromMe"})
    if is_group:
        return jsonify({"ok": True, "ignored": True, "reason": "group"})

    numero = _extrai_numero(remote_jid)
    msg = data.get("message") or {}
    texto = _extrai_texto(msg).strip()
    push_name = data.get("pushName") or ""

    if not texto:
        log(f"webhook: msg sem texto plain ({numero}) — ignorando", "INFO")
        return jsonify({"ok": True, "ignored": True, "reason": "sem_texto"})
    if not numero:
        return jsonify({"ok": True, "ignored": True, "reason": "sem_numero"})

    log(f"webhook ← {numero} ({push_name}): {texto[:80]!r}")
    # Despacha pro responder em thread separada
    threading.Thread(target=processar_async,
                     args=(numero, texto, push_name),
                     daemon=True).start()
    return jsonify({"ok": True, "accepted": True, "numero": numero})


if __name__ == "__main__":
    log(f"Scout Webhook subindo em :{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
