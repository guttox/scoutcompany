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
from _common import (
    dispatch_dry_run, env, load_conversa, load_env, log, mensagem_ja_processada,
)

load_env()

from flask import Flask, jsonify, request

import whatsapp_responder

app = Flask(__name__)
# Railway injeta $PORT em runtime; local usa WEBHOOK_PORT (default 5005)
PORT = int(env("PORT") or env("WEBHOOK_PORT", "5005"))


def _extrai_numero(remote_jid):
    """Pega só os dígitos antes do @ no jid (ex '5511940670464@s.whatsapp.net')."""
    if not remote_jid:
        return ""
    base = remote_jid.split("@", 1)[0]
    return "".join(c for c in base if c.isdigit())


def _is_truthy_flag(v):
    """Aceita True, 'true', 'True', 1, '1' como verdadeiro. Cobre bug onde
    Evolution às vezes manda fromMe como string ao invés de bool."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return False


def _is_eco_da_nossa_resposta(numero, texto_recebido):
    """Defesa nº3: se o texto recebido é IDÊNTICO à última resposta que enviamos
    pra esse número (nas últimas 5 msgs do histórico), é eco/loopback — ignora.

    Cobre o cenário onde Evolution emite MESSAGES_UPSERT pra nossa própria
    mensagem enviada SEM marcar fromMe (bug observado em v2.3.7).
    """
    try:
        conv = load_conversa(numero)
    except Exception:
        return False
    if not texto_recebido:
        return False
    txt_recv = texto_recebido.strip()
    # Compara contra últimas 5 mensagens assistant
    assistants = [m for m in conv.get("mensagens", [])
                  if m.get("role") == "assistant"][-5:]
    for m in assistants:
        if (m.get("content") or "").strip() == txt_recv:
            return True
    return False


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
    participant = key.get("participant") or ""
    is_group = "@g.us" in remote_jid
    message_id = key.get("id") or ""

    # ───────────────────────────────────────────────────────────
    # CAMADA 1 — fromMe (robusto: aceita bool ou string)
    # ───────────────────────────────────────────────────────────
    from_me = _is_truthy_flag(key.get("fromMe"))
    if from_me:
        log(f"[IGNORADO] mensagem própria (fromMe=true) id={message_id[:12]} jid={remote_jid}", "INFO")
        return jsonify({"ok": True, "ignored": True, "reason": "fromMe"})

    # ───────────────────────────────────────────────────────────
    # CAMADA 2 — remetente é o próprio número Scout
    # (cobre bug onde Evolution não seta fromMe corretamente)
    # ───────────────────────────────────────────────────────────
    scout_num = "".join(c for c in (env("WHATSAPP_SCOUT", "") or "") if c.isdigit())
    remote_digits = _extrai_numero(remote_jid)
    participant_digits = _extrai_numero(participant)
    if scout_num and (remote_digits == scout_num or participant_digits == scout_num):
        log(f"[IGNORADO] mensagem própria (número Scout) id={message_id[:12]} "
            f"remote={remote_digits} participant={participant_digits}", "INFO")
        return jsonify({"ok": True, "ignored": True, "reason": "own_number"})

    if is_group:
        return jsonify({"ok": True, "ignored": True, "reason": "group"})

    # ★ DEDUP IDEMPOTENTE: Evolution às vezes entrega o mesmo evento 2x.
    # Cache 60s via Redis (SETNX atomic) ou in-memory fallback.
    # Tem que rodar ANTES do thread spawn — senão 2 threads sobem com mesmo id.
    if message_id and mensagem_ja_processada(message_id):
        log(f"webhook ← {remote_jid} dup ({message_id}) — silenciando", "INFO")
        return jsonify({"ok": True, "ignored": True, "reason": "duplicate", "message_id": message_id})

    numero = _extrai_numero(remote_jid)
    msg = data.get("message") or {}
    texto = _extrai_texto(msg).strip()
    push_name = data.get("pushName") or ""

    if not texto:
        log(f"webhook: msg sem texto plain ({numero}) — ignorando", "INFO")
        return jsonify({"ok": True, "ignored": True, "reason": "sem_texto"})
    if not numero:
        return jsonify({"ok": True, "ignored": True, "reason": "sem_numero"})

    # ───────────────────────────────────────────────────────────
    # CAMADA 3 — eco de texto: se o texto recebido bate com alguma
    # das últimas respostas que enviamos pra esse número, é loopback.
    # ───────────────────────────────────────────────────────────
    if _is_eco_da_nossa_resposta(numero, texto):
        log(f"[IGNORADO] mensagem própria (eco de resposta) "
            f"id={message_id[:12]} numero={numero}: {texto[:60]!r}", "INFO")
        return jsonify({"ok": True, "ignored": True, "reason": "echo_resposta"})

    log(f"webhook ← {numero} ({push_name}) [{message_id[:10]}]: {texto[:80]!r}")
    # Despacha pro responder em thread separada
    threading.Thread(target=processar_async,
                     args=(numero, texto, push_name),
                     daemon=True).start()
    return jsonify({"ok": True, "accepted": True, "numero": numero,
                    "message_id": message_id})


if __name__ == "__main__":
    log(f"Scout Webhook subindo em :{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
