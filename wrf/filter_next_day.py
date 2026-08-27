#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


EXPECTED_HOURS_PER_DAY = 24


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='wrf_publish')
    parser.add_argument('--days', type=int, default=2)
    args = parser.parse_args()

    if args.days not in (1, 2):
        raise SystemExit('--days precisa ser 1 ou 2')

    root = Path(args.root)
    meta_path = root / 'metadata.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))

    if meta.get('reflectivitySource') != 'REFL_10CM_NATIVE':
        raise SystemExit(f"Fonte de refletividade invalida: {meta.get('reflectivitySource')}")

    brt = ZoneInfo('America/Sao_Paulo')
    init_utc = datetime.fromisoformat(meta['initTime'].replace('Z', '+00:00'))
    first_date = init_utc.astimezone(brt).date() + timedelta(days=1)
    target_dates = [first_date + timedelta(days=n) for n in range(args.days)]
    target_set = set(target_dates)

    keep = []
    keep_files: set[str] = set()
    hours_by_date = {date: set() for date in target_dates}

    for frame in meta.get('frames', []):
        if frame.get('reflectivitySource') != 'REFL_10CM_NATIVE':
            raise SystemExit(f'Frame sem REFL_10CM nativo: {frame}')
        valid = datetime.fromisoformat(frame['validTime'].replace('Z', '+00:00')).astimezone(brt)
        if valid.date() in target_set:
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
        if count != EXPECTED_HOURS_PER_DAY or hours != set(range(24)):
            errors.append(f'{date}: {count} quadros, horas {sorted(hours)}')

    if errors:
        raise SystemExit(
            f'Esperados {24 * args.days} horarios completos em BRT; ' + '; '.join(errors)
        )

    model = str(meta.get('model') or 'gfs').lower()
    model_dir = root / model
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
    meta['temporalResolutionMinutes'] = 60
    meta['localHours'] = list(range(24)) if args.days == 1 else list(range(24)) * 2
    meta['daysPublished'] = args.days
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    print('MODELO:', model)
    print('DIAS:', ', '.join(str(x) for x in target_dates))
    print('QUADROS:', len(keep))
    print('RESOLUCAO TEMPORAL: 1 hora')
    for frame in keep:
        print(f"F{int(frame['forecastHour']):03d} {frame['validTime']} => {frame['localValidTime']}")


if __name__ == '__main__':
    main()
