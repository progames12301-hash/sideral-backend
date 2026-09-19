"""Operational SHARPpy wrapper used by GitHub Actions.

All meteorological calculations and the complete visual product are produced
inside the GitHub runner. The browser only receives finished PNG/GIF/JSON.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.skewt import generate_sharppy_product as g
from tools.skewt.native_spc_render import render_native_spc

g.PL_PARAMS = ["t", "r", "u", "v", "gh"]
g.render_with_sharppy = render_native_spc

g.main()
