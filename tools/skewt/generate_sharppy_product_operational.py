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

class CycleClient(_original_client):
    def retrieve(self, *args, **kwargs):
        if os.environ.get('SKEWT_CYCLE') and 'date' not in kwargs and 'time' not in kwargs:
            import datetime as dt
            kwargs['date'] = dt.datetime.utcnow().strftime('%Y-%m-%d')
            kwargs['time'] = int(os.environ['SKEWT_CYCLE'])
        return super().retrieve(*args, **kwargs)

g.Client = CycleClient

g.main()
