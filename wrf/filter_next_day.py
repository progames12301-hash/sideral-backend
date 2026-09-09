#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='wrf_publish')
    parser.add_argument('--days', type=int, default=2)
    parser.add_argument('--interval-hours', type=int, choices=(1, 3), default=1)
    file_mode = parser.add_mutually_exclusive_group()
    file_mode.add_argument(
        '--preserve-files',
        dest='preserve_files',
        action='store_true',
        help='Mantem todos os arquivos de forecast no diretorio do modelo; filtra apenas metadata.json.',
    )
    file_mode.add_argument(
        '--prune-files',
        dest='preserve_files',
        action='store_false',
        help='Remove arquivos que ficam fora da janela local publicada.',
    )
    parser.set_defaults(preserve_files=True)
    args = parser.parse_args()

    if args.days not in (1, 2):
        raise SystemExit('--days precisa ser 1 ou 2')

    root = Path(args.root)
    meta_path = root / 'metadata.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))

    if meta.get('reflectivitySource') != 'REFL_10CM_NATIVE':
        raise SystemExit(f"Fonte de refletividade invalida: {meta.get('reflectivitySource')}")

    original_frames = list(meta.get('frames', []))
    model = str(meta.get('model') or 'gfs').lower()
    model_dir = root / model

    # Quando o merge realmente contem F060, a publicacao nao pode prosseguir
    # se o arquivo correspondente estiver ausente. Isso evita publicar uma
    # branch que inevitavelmente responderia HTTP 404 ao frontend.
    original_hours = {int(frame.get('forecastHour', -1)) for frame in original_frames}
    if 60 in original_hours and not (model_dir / 'f060.json.gz').is_file():
        raise SystemExit(f'F060 ausente antes da publicacao: {model_dir / "f060.json.gz"}')

    brt = ZoneInfo('America/Sao_Paulo')
    # O metadata publicado acompanha os proximos dias em BRT. Os arquivos
    # completos permanecem disponiveis por padrao para que URLs fixas como
    # F060/F063/F066/F069/F072 nao virem 404 fora da janela visual.
    # Keep the forecast window tied to initialization even if queued segments
    # finish on a later calendar day. F000-F072 covers these two complete days.
    init = datetime.fromisoformat(meta['initTime'].replace('Z', '+00:00'))
    if init.tzinfo is None:
        raise SystemExit('initTime precisa incluir fuso horario')
    first_date = init.astimezone(timezone.utc).date() + timedelta(days=1)
    target_dates = [first_date + timedelta(days=n) for n in range(args.days)]
    target_set = set(target_dates)

    keep = []
    keep_files: set[str] = set()
    hours_by_date = {date: set() for date in target_dates}

    expected_hours = set(range(0, 24, args.interval_hours))
    for frame in sorted(original_frames, key=lambda item: item['validTime']):
        if frame.get('reflectivitySource') != 'REFL_10CM_NATIVE':
            raise SystemExit(f'Frame sem REFL_10CM nativo: {frame}')
        valid = datetime.fromisoformat(frame['validTime'].replace('Z', '+00:00')).astimezone(brt)
        if valid.date() in target_set and valid.hour in expected_hours and valid.minute == 0 and valid.second == 0:
            frame = dict(frame)
            frame['index'] = len(keep)
            frame['localValidTime'] = valid.isoformat()
            frame['localHour'] = valid.hour
            frame['localDate'] = valid.date().isoformat()
            frame['localDayIndex'] = target_dates.index(valid.date())
            keep.append(frame)
            keep_files.add(frame['file'])
            hours_by_date[valid.date()].add(valid.hour)

    errors = []
    for date in target_dates:
        hours = hours_by_date[date]
        count = sum(1 for frame in keep if frame['localDate'] == date.isoformat())
        if count != len(expected_hours) or hours != expected_hours:
            errors.append(f'{date}: {count} quadros, horas {sorted(hours)}')

    if errors:
        raise SystemExit(
            f'Esperados {len(expected_hours) * args.days} horarios em BRT a cada {args.interval_hours} h; ' + '; '.join(errors)
        )

    if not args.preserve_files:
        for path in model_dir.glob('*.json.gz'):
            rel = path.relative_to(root).as_posix()
            if rel not in keep_files:
                path.unlink()

    meta['frames'] = keep
    meta['frameCount'] = len(keep)
    meta['forecastLocalDate'] = target_dates[0].isoformat()
    meta['forecastLocalDates'] = [date.isoformat() for date in target_dates]
    meta['timezone'] = 'America/Sao_Paulo'
    meta['scope'] = 'next_two_local_days' if args.days == 2 else 'next_local_day'
    meta['temporalResolutionMinutes'] = args.interval_hours * 60
    meta['localHours'] = [frame['localHour'] for frame in keep]
    meta['daysPublished'] = args.days
    meta['forecastFilesPreserved'] = bool(args.preserve_files)
    meta['availableForecastHours'] = sorted(original_hours)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    print('MODELO:', model)
    print('DIAS:', ', '.join(str(x) for x in target_dates))
    print('QUADROS NO METADATA:', len(keep))
    print('ARQUIVOS COMPLETOS PRESERVADOS:', 'sim' if args.preserve_files else 'nao')
    print('FORECAST HOURS DISPONIVEIS:', sorted(original_hours))
    print(f'RESOLUCAO TEMPORAL: {args.interval_hours} h')
    for frame in keep:
        print(f"F{int(frame['forecastHour']):03d} {frame['validTime']} => {frame['localValidTime']}")


if __name__ == '__main__':
    main()
