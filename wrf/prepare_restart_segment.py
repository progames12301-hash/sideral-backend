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
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-hour', type=int, required=True)
    parser.add_argument('--end-hour', type=int, required=True)
    parser.add_argument('--restart-file')
    parser.add_argument('--root', default='.')
    args = parser.parse_args()

    if args.start_hour < 0 or args.end_hour <= args.start_hour:
        raise SystemExit('Intervalo invalido')
    if args.start_hour % 3 or args.end_hour % 3 or args.end_hour > 72:
        raise SystemExit('Start/end precisam ser multiplos de 3 h entre F000 e F072')

    root = Path(args.root).resolve()
    common = root / 'wrf' / 'run_wrf_with_source.sh'
    text = common.read_text(encoding='utf-8')

    text = replace_once(
        text,
        'WRF_START_HOUR="${WRF_START_HOUR:-0}"',
        f'WRF_START_HOUR="${{WRF_START_HOUR:-{args.start_hour}}}"',
        'inicio do segmento',
    )
    text = replace_once(
        text,
        'WRF_END_HOUR="${WRF_END_HOUR:-$WRF_RUN_HOURS}"',
        f'WRF_END_HOUR="${{WRF_END_HOUR:-{args.end_hour}}}"',
        'fim do segmento',
    )

    restart_flag = '.true.' if args.start_hour > 0 else '.false.'
    restart_minutes = (args.end_hour - args.start_hour) * 60
    text = replace_once(
        text,
        ' restart = .false.,',
        f' restart = {restart_flag},\n restart_interval = {restart_minutes},\n write_hist_at_0h_rst = .true.,',
        'restart namelist',
    )

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

