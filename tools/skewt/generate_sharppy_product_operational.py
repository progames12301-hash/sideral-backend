"""Operational SHARPpy entrypoint for GitHub Actions.

The browser never renders the sounding. This wrapper selects the nearest
ECMWF IFS 0.25-degree grid point and pins the requested ECMWF cycle.
"""
from pathlib import Path
import argparse
import importlib.util
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
SKEWT_DIR = ROOT / 'tools' / 'skewt'


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def snap025(value: float) -> float:
    import math
    return math.floor(value * 4.0 + 0.5) / 4.0


g = load_module('sideral_skewt_generator', SKEWT_DIR / 'generate_sharppy_product.py')
renderer = load_module('sideral_native_spc_render', SKEWT_DIR / 'native_spc_render.py')
g.PL_PARAMS = ['t', 'r', 'u', 'v', 'w', 'gh']
g.render_with_sharppy = renderer.render_native_spc

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--lat', type=float, required=True)
parser.add_argument('--lon', type=float, required=True)
parser.add_argument('--label', default='Brasil')
parser.add_argument('--out', default='skewt-out')
parser.add_argument('--cycle', choices=['00', '06', '12', '18'], default=None)
known, rest = parser.parse_known_args()

# Any location can be requested. The sounding is tied to the nearest native
# ECMWF IFS 0.25-degree grid point instead of a fixed city list.
snapped_lat = snap025(known.lat)
snapped_lon = snap025(known.lon)

sys.argv = [
    sys.argv[0],
    '--lat', str(snapped_lat),
    '--lon', str(snapped_lon),
    '--label', known.label,
    '--out', known.out,
] + rest

if known.cycle:
    os.environ['SKEWT_CYCLE'] = known.cycle

_original_client = g.Client


def is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return ('503' in text and ('slowdown' in text or 'slow down' in text)) or 'slowdown' in text


class CycleClient(_original_client):
    """ECMWF client with controlled mirror failover for S3 SlowDown."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('maximum_retries', 2)
        kwargs.setdefault('retry_after', (5, 30, 2))
        kwargs.setdefault('use_server_retry_after', True)
        super().__init__(*args, **kwargs)
        primary = kwargs.get('source', os.getenv('ECMWF_SOURCE', 'aws'))
        configured = [x.strip() for x in os.getenv('ECMWF_FALLBACK_SOURCES', 'azure,google').split(',') if x.strip()]
        self._sideral_sources = []
        for source in [primary, *configured]:
            if source not in self._sideral_sources:
                self._sideral_sources.append(source)

    def retrieve(self, *args, **kwargs):
        if os.environ.get('SKEWT_CYCLE') and 'date' not in kwargs and 'time' not in kwargs:
            import datetime as dt
            kwargs['date'] = dt.datetime.utcnow().strftime('%Y-%m-%d')
            kwargs['time'] = int(os.environ['SKEWT_CYCLE'])

        last_exc = None
        for index, source in enumerate(self._sideral_sources):
            try:
                if index == 0:
                    return super().retrieve(*args, **kwargs)

                fallback = _original_client(
                    source=source,
                    model='ifs',
                    resol='0p25',
                    maximum_retries=2,
                    retry_after=(5, 30, 2),
                    use_server_retry_after=True,
                )
                print(f'[Sideral Skew-T] ECMWF mirror fallback: {source}', flush=True)
                return fallback.retrieve(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if not is_rate_limit_error(exc):
                    raise
                if index + 1 < len(self._sideral_sources):
                    print(
                        f'[Sideral Skew-T] ECMWF {source} returned 503 SlowDown; '
                        f'trying next mirror.',
                        flush=True,
                    )

        raise RuntimeError(
            'ECMWF Open Data mirrors exhausted after repeated 503 SlowDown responses.'
        ) from last_exc


g.Client = CycleClient

g.main()
