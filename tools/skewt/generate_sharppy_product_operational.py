"""Operational SHARPpy wrapper used by GitHub Actions.

All meteorological calculations and the complete visual product are produced
inside the GitHub runner. The browser only receives finished PNG/GIF/JSON.
"""
from tools.skewt import generate_sharppy_product as g
from tools.skewt.native_spc_render import render_native_spc

# Use only the pressure-level parameters that are published reliably by the
# ECMWF Open Data operational index.
g.PL_PARAMS = ["t", "r", "u", "v", "gh"]

# Replace the generator's simple two-panel renderer with the native SHARPpy
# SPC-style widget arrangement. Nothing is reconstructed by the browser.
g.render_with_sharppy = render_native_spc

g.main()
