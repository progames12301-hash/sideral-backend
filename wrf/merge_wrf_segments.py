#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--input', action='append', required=True, dest='inputs')
    p.add_argument('--output', required=True)
    args = p.parse_args()

    roots = [Path(x) for x in args.inputs]
    out = Path(args.output)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    by_hour: dict[int, tuple[dict, Path]] = {}
    template = None

    for root in roots:
        meta_path = root / 'metadata.json'
        if not meta_path.exists():
            raise SystemExit(f'Metadata ausente: {meta_path}')
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        if meta.get('reflectivitySource') != 'REFL_10CM_NATIVE':
            raise SystemExit(f'Refletividade nao nativa em {root}')
        if template is None:
            template = meta
        for frame in meta.get('frames', []):
            hour = int(frame['forecastHour'])
            file_path = root / frame['file']
            if not file_path.exists():
                raise SystemExit(f'Frame ausente: {file_path}')
            # O segmento mais recente pode repetir o horario de restart; ele vence.
            by_hour[hour] = (frame, file_path)

    if template is None or not by_hour:
        raise SystemExit('Nenhum frame para unir')

    frames = []
    for hour in sorted(by_hour):
        frame, src = by_hour[hour]
        rel = Path(frame['file'])
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        frame = dict(frame)
        frame['index'] = len(frames)
        frames.append(frame)

    merged = dict(template)
    merged['frames'] = frames
    merged['frameCount'] = len(frames)
    merged['forecastHourStart'] = min(by_hour)
    merged['forecastHourEnd'] = max(by_hour)
    merged['segmentedRun'] = True
    (out / 'metadata.json').write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')

    print('WRF SEGMENTOS UNIDOS:', len(frames), 'frames')
    print('HORIZONTE:', min(by_hour), 'a', max(by_hour), 'h')


if __name__ == '__main__':
    main()
