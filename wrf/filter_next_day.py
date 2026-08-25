#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


EXPECTED_HOURS = 24


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='wrf_publish')
    args = parser.parse_args()

    root = Path(args.root)
    meta_path = root / 'metadata.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))

    if meta.get('reflectivitySource') != 'REFL_10CM_NATIVE':
        raise SystemExit(f"Fonte de refletividade invalida: {meta.get('reflectivitySource')}")

    brt = ZoneInfo('America/Sao_Paulo')
    init_utc = datetime.fromisoformat(meta['initTime'].replace('Z', '+00:00'))
    target_date = init_utc.astimezone(brt).date() + timedelta(days=1)

    keep = []
    keep_files: set[str] = set()
    local_hours: set[int] = set()
    for frame in meta.get('frames', []):
        if frame.get('reflectivitySource') != 'REFL_10CM_NATIVE':
            raise SystemExit(f'Frame sem REFL_10CM nativo: {frame}')
        valid = datetime.fromisoformat(frame['validTime'].replace('Z', '+00:00')).astimezone(brt)
        if valid.date() == target_date:
            frame['index'] = len(keep)
            frame['localValidTime'] = valid.isoformat()
            frame['localHour'] = valid.hour
            keep.append(frame)
            keep_files.add(frame['file'])
            local_hours.add(valid.hour)

    if len(keep) != EXPECTED_HOURS or local_hours != set(range(24)):
        raise SystemExit(
            f'Esperados 24 horarios (00-23 BRT) do proximo dia; '
            f'encontrados {len(keep)} quadros e horas {sorted(local_hours)}'
        )

    for path in (root / 'gfs').glob('*.json.gz'):
        rel = path.relative_to(root).as_posix()
        if rel not in keep_files:
            path.unlink()

    meta['frames'] = keep
    meta['frameCount'] = len(keep)
    meta['forecastLocalDate'] = target_date.isoformat()
    meta['timezone'] = 'America/Sao_Paulo'
    meta['scope'] = 'next_local_day'
    meta['temporalResolutionMinutes'] = 60
    meta['localHours'] = list(range(24))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    print('PROXIMO DIA:', target_date)
    print('RESOLUCAO TEMPORAL: 1 hora')
    for frame in keep:
        print(f"F{int(frame['forecastHour']):03d} {frame['validTime']} => {frame['localValidTime']}")


if __name__ == '__main__':
    main()
