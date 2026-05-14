"""
Corrige o contador diário do dispatcher quando crashes inflaram disparos.log.

CONTEXTO. O dispatcher conta tentativas do dia via _tentativas_hoje(), que lê
logs/disparos.log. Quando o NameError do tentados_hoje crasheou o loop, a chamada
para a Evolution já tinha rodado e a linha OK/FAIL/DRY_RUN já estava no log — mas
a entrega real pro WhatsApp pode não ter completado (Evolution retornou 200, mas
o cliente final não recebeu). Resultado: o contador inflou.

ESTE SCRIPT:
  1. Conta envios reais no pipeline.csv (status="Abordado" E data_envio_site=hoje).
  2. Lê a contagem atual usada pelo dispatcher (disparos.log).
  3. Se inflou, reescreve disparos.log mantendo somente as X primeiras linhas de
     tentativa de hoje. Outras datas e linhas administrativas (RODADA) ficam
     intactas. Sempre faz backup antes.
  4. Grava trilha de auditoria em config.json sob "correcoes_contador".
  5. Imprime: "Enviados reais hoje: X — contador corrigido de Y para X"

Uso:
  python scripts/reset_daily_counter.py
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    DISPAROS_LOG, PIPELINE_CSV, read_config, read_csv, write_config,
)


def _contar_pipeline_real(hoje_iso):
    pipeline = read_csv(PIPELINE_CSV)
    n = 0
    for row in pipeline:
        status = (row.get("status") or "").strip()
        data_envio = (row.get("data_envio_site") or "").strip()
        if status == "Abordado" and data_envio.startswith(hoje_iso):
            n += 1
    return n


def _eh_linha_tentativa(line):
    if " RODADA " in line:
        return False
    return ("OK " in line) or ("FAIL " in line) or ("DRY_RUN " in line)


def _contar_disparos_log(hoje_iso):
    if not DISPAROS_LOG.exists():
        return 0
    n = 0
    with open(DISPAROS_LOG, encoding="utf-8") as f:
        for line in f:
            if not line.startswith(f"[{hoje_iso}"):
                continue
            if _eh_linha_tentativa(line):
                n += 1
    return n


def _reescrever_disparos_log(hoje_iso, x_real):
    outros = []
    hoje_tentativa = []
    hoje_outras = []
    with open(DISPAROS_LOG, encoding="utf-8") as f:
        for line in f:
            if not line.startswith(f"[{hoje_iso}"):
                outros.append(line)
                continue
            if _eh_linha_tentativa(line):
                hoje_tentativa.append(line)
            else:
                hoje_outras.append(line)

    ts_bak = datetime.now().strftime("%H%M%S")
    backup = DISPAROS_LOG.with_name(f"disparos.log.bak-{hoje_iso}-{ts_bak}")
    with open(backup, "w", encoding="utf-8") as f:
        f.writelines(outros + hoje_outras + hoje_tentativa)

    mantidas = hoje_tentativa[:x_real]
    descartadas = len(hoje_tentativa) - len(mantidas)

    with open(DISPAROS_LOG, "w", encoding="utf-8") as f:
        f.writelines(outros + hoje_outras + mantidas)

    return backup, descartadas


def main():
    hoje_iso = date.today().isoformat()
    x = _contar_pipeline_real(hoje_iso)
    y = _contar_disparos_log(hoje_iso)

    print(f"Pipeline (status=Abordado AND data_envio_site={hoje_iso}): {x}")
    print(f"Disparos.log (contador atual usado pelo dispatcher): {y}")

    if y <= x:
        print(f"Enviados reais hoje: {x} — contador já está OK ({y} ≤ {x}), nada a corrigir.")
        return

    backup, descartadas = _reescrever_disparos_log(hoje_iso, x)
    print(f"Backup criado: {backup.name}")
    print(f"Linhas de tentativa descartadas: {descartadas}")

    cfg = read_config()
    cfg.setdefault("correcoes_contador", []).append({
        "data": hoje_iso,
        "antes": y,
        "depois": x,
        "linhas_descartadas": descartadas,
        "backup": backup.name,
        "corrigido_em": datetime.now().isoformat(timespec="seconds"),
    })
    write_config(cfg)

    print(f"Enviados reais hoje: {x} — contador corrigido de {y} para {x}")


if __name__ == "__main__":
    main()
