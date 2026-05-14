"""
Scout — health check do host VPS. Roda toda hora cheia via cron.

Verifica:
  • Containers do docker compose: se algum parou, dá `docker compose up -d <svc>`
    e alerta no Telegram.
  • CPU: load average de 5min vs nproc. Se > 80% (load5/nproc > 0.80), alerta.
    Usa /proc/loadavg porque já é uma janela móvel de 5min — não precisa
    sustentar amostragem dentro do script (cron horário não permitiria).
  • Disco: uso de / > 85% → alerta.

Dedup de alertas: gravado em logs/health_check_state.json. Mesmo alerta só
re-dispara depois de COOLDOWN_HORAS (default 6h) ou se voltar ao normal antes.

Standalone — não importa de scripts/_common.py pra rodar no /usr/bin/python3
do host (sem o venv do container).

Uso:
  python3 /opt/scout/scripts/health_check.py            # roda
  python3 /opt/scout/scripts/health_check.py --dry-run  # só lista, não restart/alerta
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/opt/scout")
ENV_FILE = ROOT / ".env"
COMPOSE_FILE = ROOT / "docker-compose.yml"
STATE_FILE = ROOT / "logs" / "health_check_state.json"

CPU_LIMIAR = 0.80           # load5/nproc — alerta se acima
DISCO_LIMIAR_PCT = 85
COOLDOWN_HORAS = 6
COMPOSE_TIMEOUT = 30


# ─── env loader (stdlib, sem depender do _common.py) ─────────────
def load_env_file():
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def stamp():
    return datetime.now().isoformat(timespec="seconds")


def log(msg):
    print(f"[{stamp()}] {msg}", flush=True)


# ─── Estado pra dedup de alertas ─────────────
def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def deve_alertar(state, chave):
    """True se nunca alertou pra essa chave ou se passou o cooldown."""
    ts = state.get(chave)
    if not ts:
        return True
    try:
        ultima = datetime.fromisoformat(ts)
    except Exception:
        return True
    return (datetime.now() - ultima) >= timedelta(hours=COOLDOWN_HORAS)


def marcar_alerta(state, chave):
    state[chave] = stamp()


def limpar_alerta(state, chave):
    state.pop(chave, None)


# ─── Telegram ─────────────
def telegram(texto, dry_run=False):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("⚠ TELEGRAM_TOKEN/CHAT_ID ausentes — alerta não enviado")
        return False
    if dry_run:
        log(f"[DRY] telegram → {texto}")
        return True
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat,
        "text": texto,
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=payload, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"✗ telegram falhou: {e}")
        return False


# ─── Docker containers ─────────────
def listar_containers():
    """Retorna lista de dicts {Service, State, Name} do compose."""
    try:
        out = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE),
             "ps", "--format", "json", "--all"],
            capture_output=True, text=True, timeout=COMPOSE_TIMEOUT, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        log(f"✗ docker compose ps falhou: {e.stderr}")
        return None
    except FileNotFoundError:
        log("✗ docker não encontrado no PATH")
        return None
    except subprocess.TimeoutExpired:
        log("✗ docker compose ps timeout")
        return None
    if not out:
        return []
    # `docker compose ps --format json` pode emitir 1 JSON ou NDJSON dependendo da versão
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        items = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return items


def restart_servico(svc, dry_run=False):
    if dry_run:
        log(f"[DRY] docker compose up -d {svc}")
        return True
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", svc],
            capture_output=True, text=True, timeout=COMPOSE_TIMEOUT, check=True,
        )
        log(f"  ↻ {svc} reiniciado")
        return True
    except subprocess.CalledProcessError as e:
        log(f"  ✗ falhou restart de {svc}: {e.stderr}")
        return False


def checar_containers(state, dry_run=False):
    items = listar_containers()
    if items is None:
        if deve_alertar(state, "docker_indisponivel"):
            telegram("🚨 Scout VPS: docker indisponível ou compose quebrado.", dry_run)
            marcar_alerta(state, "docker_indisponivel")
        return
    limpar_alerta(state, "docker_indisponivel")

    caidos = []
    for c in items:
        svc = c.get("Service") or c.get("Name", "?")
        estado = (c.get("State") or "").lower()
        # estados saudáveis: "running". "restarting" também é transitório.
        if estado not in ("running", "restarting"):
            caidos.append((svc, estado))

    if not caidos:
        limpar_alerta(state, "containers_caidos")
        log(f"✓ {len(items)} container(s) up")
        return

    nomes = ", ".join(f"{s}({e})" for s, e in caidos)
    log(f"✗ caídos: {nomes}")
    for svc, _ in caidos:
        restart_servico(svc, dry_run)

    if deve_alertar(state, "containers_caidos"):
        telegram(f"🚨 Scout VPS: container(s) caíram e foram reiniciados: {nomes}",
                 dry_run)
        marcar_alerta(state, "containers_caidos")


# ─── CPU (load avg 5min) ─────────────
def checar_cpu(state, dry_run=False):
    try:
        load1, load5, load15 = (
            float(x) for x in Path("/proc/loadavg").read_text().split()[:3]
        )
    except Exception as e:
        log(f"✗ /proc/loadavg ilegível: {e}")
        return
    nproc = os.cpu_count() or 1
    ratio = load5 / nproc
    log(f"CPU load5={load5:.2f} nproc={nproc} ratio={ratio:.2f}")
    chave = "cpu_alta"
    if ratio > CPU_LIMIAR:
        if deve_alertar(state, chave):
            telegram(
                f"⚠️ Scout VPS: CPU sustentada acima de {int(CPU_LIMIAR*100)}% "
                f"nos últimos 5min (load5={load5:.2f}, {nproc} CPUs).",
                dry_run,
            )
            marcar_alerta(state, chave)
    else:
        limpar_alerta(state, chave)


# ─── Disco ─────────────
def checar_disco(state, dry_run=False):
    total, _used, _free = shutil.disk_usage("/")
    used = total - _free
    pct = (used / total) * 100
    log(f"Disco /: {pct:.1f}% usado ({used // (1024**3)}G/{total // (1024**3)}G)")
    chave = "disco_cheio"
    if pct > DISCO_LIMIAR_PCT:
        if deve_alertar(state, chave):
            telegram(
                f"⚠️ Scout VPS: disco / em {pct:.1f}% (limite {DISCO_LIMIAR_PCT}%).",
                dry_run,
            )
            marcar_alerta(state, chave)
    else:
        limpar_alerta(state, chave)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Não reinicia containers nem manda Telegram — só loga")
    args = parser.parse_args()

    load_env_file()
    state = load_state()

    checar_containers(state, dry_run=args.dry_run)
    checar_cpu(state, dry_run=args.dry_run)
    checar_disco(state, dry_run=args.dry_run)

    if not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"✗ health_check crashou: {e}")
        sys.exit(1)
