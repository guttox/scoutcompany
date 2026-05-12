"""
Lembrete único: dispara em 02/06/2026 às 10h pra avisar que está na hora de
reavaliar a habilitação de domingo (depois de 3 semanas de aquecimento).

Após disparar, se auto-remove do launchd e apaga o próprio plist.
"""
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import env, load_env, log


MENSAGEM = (
    "🔔 Lembrete Scout — reavaliar DOMINGO\n\n"
    "Hoje (02/06) marca 3 semanas que o número Scout está aquecendo em LIVE.\n"
    "Já dá pra considerar habilitar disparos no domingo até 12h se quiser.\n\n"
    "Análise rápida antes de decidir:\n"
    "• Conferir taxa de respostas dos últimos 7 dias (deve estar > 5%)\n"
    "• Conferir se houve falhas (volume.log) — qualquer dia com >30% falha = abortar\n"
    "• Se taxa OK + sem falhas: pode ativar começando com segmentos restaurante/petshop\n\n"
    "Pra ativar: me chama no chat e eu edito o SEGMENT_WINDOWS + a regra de domingo no _common.py."
)


def enviar():
    token = env("TELEGRAM_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("Telegram não configurado", "ERROR")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id, "text": MENSAGEM,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=payload, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log(f"falha telegram: {e}", "ERROR")
        return False


def auto_remover():
    """Descarrega o LaunchAgent e apaga o plist — o lembrete é one-shot."""
    plist = Path.home() / "Library/LaunchAgents/com.scout.lembrete-domingo.plist"
    try:
        subprocess.run(["launchctl", "unload", str(plist)],
                       capture_output=True, timeout=10)
    except Exception:
        pass
    try:
        if plist.exists():
            plist.unlink()
            log(f"plist removido: {plist}")
    except Exception as e:
        log(f"falha ao remover plist: {e}", "WARN")


def main():
    load_env()
    ok = enviar()
    log(f"Lembrete domingo enviado: {ok}")
    auto_remover()


if __name__ == "__main__":
    main()
