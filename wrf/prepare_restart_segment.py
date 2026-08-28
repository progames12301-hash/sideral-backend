#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f'Trecho nao encontrado para {label}')
    return text.replace(old, new, 1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--start-hour', type=int, required=True)
    p.add_argument('--end-hour', type=int, required=True)
    p.add_argument('--restart-file')
    p.add_argument('--root', default='.')
    args = p.parse_args()

    if args.start_hour < 0 or args.end_hour <= args.start_hour:
        raise SystemExit('Intervalo invalido')
    if args.start_hour % 3 or args.end_hour % 3:
        raise SystemExit('Start/end precisam ser multiplos de 3 h')

    root = Path(args.root).resolve()
    common = root / 'wrf' / 'run_wrf_with_source.sh'
    text = common.read_text(encoding='utf-8')

    # Permite horizontes usados pelos segmentos, sem alterar a fisica do WRF.
    text = text.replace('  6|42|45) ;;', '  6|18|21|24|36|42|45|48|69|72) ;;')
    text = text.replace('WRF_RUN_HOURS precisa ser 6, 42 ou 45', 'WRF_RUN_HOURS precisa ser 6, 18, 21, 24, 36, 42, 45, 48, 69 ou 72')

    insert = ': "${WRF_RUN_HOURS:?WRF_RUN_HOURS ausente}"\n'
    replacement = insert + f'WRF_START_HOURS="{args.start_hour}"\nWRF_SEGMENT_HOURS="{args.end_hour - args.start_hour}"\n'
    text = replace_once(text, insert, replacement, 'variaveis segmento')

    # WPS/WRF comeca no horario do restart, mas o forecastHour continua relativo
    # a rodada original por meio de RUN_DATE/RUN_CYCLE no run.env.
    base = '${RUN_DATE} ${RUN_CYCLE}:00 UTC'
    start = f'${{RUN_DATE}} ${{RUN_CYCLE}}:00 UTC +{args.start_hour} hours'
    text = text.replace(f'date -u -d "{base}"', f'date -u -d "{start}"')

    # As linhas END usam o horizonte absoluto da rodada e devem continuar assim.
    # O replace acima tambem atingiria o prefixo das expressoes END; restaura-as.
    text = text.replace(
        f'date -u -d "{start} +${{WRF_RUN_HOURS}} hours"',
        f'date -u -d "{base} +${{WRF_RUN_HOURS}} hours"'
    )

    text = replace_once(
        text,
        ' run_hours = ${WRF_RUN_HOURS},',
        ' run_hours = ${WRF_SEGMENT_HOURS},',
        'run_hours segmento'
    )

    restart_flag = '.true.' if args.start_hour > 0 else '.false.'
    restart_minutes = (args.end_hour - args.start_hour) * 60
    text = replace_once(
        text,
        ' restart = .false.,',
        f' restart = {restart_flag},\n restart_interval = {restart_minutes},\n write_hist_at_0h_rst = .true.,',
        'restart namelist'
    )

    # Depois do real.exe, injeta o wrfrst do segmento anterior. O real.exe gera
    # wrfbdy coerente com o novo intervalo; o estado atmosferico vem do restart.
    anchor = '    test -f wrfinput_d01\n    test -f wrfbdy_d01\n\n    echo "=== WRF 4 KM / REFL_10CM NATIVO ==="'
    inject = '''    test -f wrfinput_d01
    test -f wrfbdy_d01

    if test -d /work/restart_input; then
      echo "=== RESTORE WRF RESTART ==="
      cp -f /work/restart_input/wrfrst_d01_* .
      ls -lh wrfrst_d01_*
    fi

    echo "=== WRF 4 KM / REFL_10CM NATIVO ==="'''
    text = replace_once(text, anchor, inject, 'restore restart')
    common.write_text(text, encoding='utf-8')

    for name in ('run_icon_wrf.sh', 'run_ecmwf_wrf.sh'):
        path = root / 'wrf' / name
        body = path.read_text(encoding='utf-8')
        body = body.replace('6|42|45)', '6|18|21|24|36|42|45|48|69|72)')
        body = body.replace('6, 42 ou 45', '6, 18, 21, 24, 36, 42, 45, 48, 69 ou 72')
        path.write_text(body, encoding='utf-8')

    restart_dir = root / 'wrf_work' / 'restart_input'
    if restart_dir.exists():
        shutil.rmtree(restart_dir)
    if args.restart_file:
        source = Path(args.restart_file)
        if not source.is_file():
            raise SystemExit(f'Restart nao encontrado: {source}')
        restart_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, restart_dir / source.name)
        print('RESTART INPUT:', restart_dir / source.name)
    elif args.start_hour > 0:
        raise SystemExit('Segmento de continuacao exige --restart-file')

    print(f'SEGMENTO WRF: F{args.start_hour:03d} -> F{args.end_hour:03d}')
    print(f'DURACAO DO SEGMENTO: {args.end_hour - args.start_hour} h')


if __name__ == '__main__':
    main()
