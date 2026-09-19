"""Operational SHARPpy entrypoint for GitHub Actions.

The wrapper loads the generator by file path so it does not depend on Python
package discovery for the repository's ``tools`` directory.
"""
from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[2]
SKEWT_DIR = ROOT / "tools" / "skewt"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


g = load_module("sideral_skewt_generator", SKEWT_DIR / "generate_sharppy_product.py")
renderer = load_module("sideral_native_spc_render", SKEWT_DIR / "native_spc_render.py")

g.PL_PARAMS = ["t", "r", "u", "v", "gh"]
g.render_with_sharppy = renderer.render_native_spc

g.main()
