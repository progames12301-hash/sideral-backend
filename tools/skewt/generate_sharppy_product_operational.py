"""Operational wrapper for the Sideral SHARPpy generator.

The upstream generator remains the single rendering implementation. This wrapper
only removes the optional pressure-level omega request because ECMWF Open Data
availability differs by stream and omega is not required to render the Skew-T.
All thermodynamic/kinematic calculations and all graphics remain SHARPpy-side.
"""
from tools.skewt import generate_sharppy_product as g

g.PL_PARAMS = ["t", "r", "u", "v", "gh"]
g.main()
