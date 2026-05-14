"""
Reparo pontual: marca como "Abordado" no pipeline.csv os números que
receberam OK/FAIL/DRY_RUN no disparos.log de hoje.

CONTEXTO. O paralelismo de dispatchers (corrigido em commit cc7892b com
flock) causava duas coisas:
  1. Mesmo número disparado várias vezes na mesma janela.
  2. write_csv concorrente sobrescrevia mudanças, e atualizar_pipeline_envio
     não conseguia gravar status="Abordado".

Resultado: 34 disparos feitos hoje, ZERO marcados na pipeline. Se o
dispatcher rodar de novo, vai re-tentar todos (numeros_com_conversa só
cobre quem RESPONDEU, ~21 dos 34).

ESTE SCRIPT:
  1. Acha o backup mais recente de disparos.log (gerado pelo reset).
  2. Extrai números únicos com tentativa de hoje.
  3. Pra cada um, marca pipeline.csv com status="Abordado" e
     data_envio_site=hoje (se ainda não tem).
  4. Imprime resumo.

One-shot. Não precisa rodar mais que uma vez.

Uso:
  python scripts/repair_today.py
  python scripts/repair_today.py --dry-run
"""
import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    DISPAROS_LOG, LOG_DIR, PIPELINE_CSV, PIPELINE_FIELDS, read_csv, write_csv,
)


def _achar_backup():
    """Pega o backup mais recente disparos.log.bak-YYYY-MM-DD-HHMMSS."""
    cands = sorted(LOG_DIR.glob("disparos.log.bak-*"), reverse=True)
    return cands[0] if cands else None


def _numeros_disparados(arquivo, hoje_iso):
    digits = set()
    with open(arquivo, encoding="utf-8") as f:
        for line in f:
            if not line.startswith(f"[{hoje_iso}"):
                continue
            if " RODADA " in line:
                continue
            if not (("OK " in line) or ("FAIL " in line) or ("DRY_RUN " in line)):
                continue
            m = re.search(r"(\d{12,13})", line)
            if m:
                digits.add(m.group(1))
    return digits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    hoje_iso = date.today().isoformat()
    fontes = []
    backup = _achar_backup()
    if backup:
        fontes.append(backup)
    if DISPAROS_LOG.exists():
        fontes.append(DISPAROS_LOG)
    if not fontes:
        print("✗ Nenhum disparos.log ou backup encontrado", file=sys.stderr)
        sys.exit(1)

    nums = set()
    for f in fontes:
        nums |= _numeros_disparados(f, hoje_iso)
    print(f"Números disparados hoje (de {len(fontes)} fonte(s)): {len(nums)}")
    if not nums:
        print("Nada a reparar.")
        return

    pipeline = read_csv(PIPELINE_CSV)
    if not pipeline:
        print("✗ Pipeline.csv vazio.", file=sys.stderr)
        sys.exit(1)

    agora_iso = datetime.now().isoformat(timespec="seconds")
    atualizados = 0
    nao_encontrados = []

    for n in nums:
        achou = False
        for row in pipeline:
            contato = "".join(c for c in (row.get("contato") or "") if c.isdigit())
            if not contato:
                continue
            if contato == n or contato in n or n in contato:
                achou = True
                status_atual = (row.get("status") or "").strip()
                if status_atual in ("", "novo", "Sem contato", "Novo"):
                    row["status"] = "Abordado"
                if not row.get("data_abordagem"):
                    row["data_abordagem"] = hoje_iso
                if not row.get("data_envio_site"):
                    row["data_envio_site"] = agora_iso
                obs = (row.get("observacao") or "").strip()
                marca = "[reparo dispatch paralelo cc7892b]"
                if marca not in obs:
                    row["observacao"] = (obs + (" | " if obs else "") + marca)[:300]
                atualizados += 1
                break
        if not achou:
            nao_encontrados.append(n)

    print(f"Marcados como Abordado: {atualizados}")
    if nao_encontrados:
        print(f"Sem match no pipeline: {len(nao_encontrados)} → {nao_encontrados[:5]}...")

    if args.dry_run:
        print("[DRY-RUN] pipeline.csv não foi alterado")
        return

    write_csv(PIPELINE_CSV, pipeline, PIPELINE_FIELDS)
    print(f"✓ Pipeline.csv salvo ({hoje_iso})")


if __name__ == "__main__":
    main()
