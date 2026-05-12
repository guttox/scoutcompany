"""
Heartbeat matinal Scout — envia mensagem rápida no Telegram avisando
que está ativo e começando o trabalho do dia.

É chamado pelo run_all.py no início do pipeline diário (cron 9h).
Também roda standalone:
  ./venv/bin/python scripts/morning_heartbeat.py
"""
import random
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    calcular_max_disparos_hoje, calcular_semana_atual,
    env, load_env, log, read_fila,
)

# 15 variações — sorteia uma diferente a cada dia (ou aleatório se quiser)
MENSAGENS = [
    "🚀 Bora vender! Já tô na ativa procurando os melhores leads de hoje.",
    "☕ Café tomado, prospects na mira. Scout em campo.",
    "💼 Hoje é dia de vender. Já tô caçando oportunidades.",
    "🎯 Ativo e trabalhando. Que venham os novos clientes.",
    "👀 De olho nos negócios. Scout em ação desde já.",
    "🔥 Mais um dia, mais leads. Já tô na correria.",
    "💪 Tô on! Hoje vamos encontrar gente boa pra fechar.",
    "📡 Sinal verde. Scout operando e prospectando.",
    "⚡ Hora de trampar. Já tô garimpando prospects pra você.",
    "🌅 Bom dia! Já tô acordado e prospectando.",
    "🏃 Não tô parado não — já estou rodando o pipeline.",
    "💰 Hoje pode ser o dia. Bora pra cima?",
    "🔍 Procurando ouro nas cidades em rodízio.",
    "📞 Tô na linha de frente. Vamos pra cima dos leads.",
    "🛠️ Engrenagens girando. Scout começando o turno.",
]


def _escolher_mensagem(seed=None):
    """Sorteia mensagem do dia. Mesma data → mesma mensagem (determinístico)."""
    base = seed or datetime.now().strftime("%Y%m%d")
    rng = random.Random(base)
    return rng.choice(MENSAGENS)


def _cidades_de_hoje():
    """Olha config.json e retorna as cidades que serão usadas hoje (sem avançar o índice)."""
    import json
    from _common import CONFIG_PATH
    if not CONFIG_PATH.exists():
        return []
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    rod = cfg.get("rodizio", {})
    return rod.get("ultimas_cidades", []) or []


def _telegram_send(text):
    token = env("TELEGRAM_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("Telegram não configurado — heartbeat não enviado", "WARN")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=payload, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log(f"Heartbeat Telegram falhou: {e}", "ERROR")
        return False


def montar_mensagem():
    msg = _escolher_mensagem()
    semana = calcular_semana_atual()
    limite = calcular_max_disparos_hoje()
    cidades = _cidades_de_hoje()
    modo = (env("DISPATCH_MODE", "DRY") or "DRY").upper()

    # Conta fila pendente
    fila = read_fila()
    pendentes = sum(1 for x in fila.get("items", []) if x.get("status") == "pendente")

    linhas = [
        msg,
        "",
        f"📅 {datetime.now().strftime('%A, %d/%m')} · semana {semana} · meta {limite} disparos",
    ]
    if cidades:
        linhas.append(f"🗺️ Hoje: {' · '.join(cidades)}")
    if pendentes:
        linhas.append(f"📋 Fila: {pendentes} aguardando janela")
    if modo == "LIVE":
        linhas.append("🟢 Modo LIVE — envio real")
    else:
        linhas.append("🟡 Modo DRY — só simulando")

    return "\n".join(linhas)


def main():
    load_env()
    texto = montar_mensagem()
    ok = _telegram_send(texto)
    if ok:
        log("✓ Heartbeat matinal enviado")
    else:
        log("✗ Heartbeat falhou", "WARN")
    return ok


if __name__ == "__main__":
    main()
