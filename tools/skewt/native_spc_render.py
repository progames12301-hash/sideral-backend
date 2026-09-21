import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QPainter
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel
from sharppy.sharptab.prof_collection import ProfCollection
from sharppy.viz import skew as _sharppy_skew
from sharppy.viz.skew import plotSkewT
from sharppy.viz.hodo import plotHodo

# Compatibility layer for SHARPpy's older Qt painting API under PyQt5.
# SHARPpy supplies numpy scalar/float coordinates and font sizes; PyQt5
# requires native Python ints for these overloads.
_OriginalQFont = _sharppy_skew.QtGui.QFont
class _CompatQFont(_OriginalQFont):
    def __new__(cls, *args, **kwargs):
        if len(args) >= 2 and isinstance(args[1], (float, int)):
            args = (args[0], int(round(float(args[1]))), *args[2:])
        return _OriginalQFont(*args, **kwargs)
_sharppy_skew.QtGui.QFont = _CompatQFont

_original_drawLine = _sharppy_skew.QPainter.drawLine if hasattr(_sharppy_skew, 'QPainter') else QPainter.drawLine

def _compat_drawLine(self, *args):
    if len(args) == 4:
        args = tuple(int(round(float(x))) for x in args)
    return _original_drawLine(self, *args)
QPainter.drawLine = _compat_drawLine


def activate(widget, pc, prof):
    widget.addProfileCollection(pc)
    try:
        widget.setActiveCollection(0)
        return
    except Exception:
        pass
    try:
        widget.setProf(prof)
    except Exception:
        pass


def render_native_spc(prof, out_dir: Path, meta: dict):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    pc = ProfCollection({'ECMWF IFS': [prof]}, [prof.date], highlight='ECMWF IFS', location=meta.get('location', 'Brasil'))
    root = QWidget()
    root.setStyleSheet('background:#000;color:#fff;')
    root.resize(1600, 980)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)
    header = QLabel(f"SIDERAL SKEW-T  |  ECMWF IFS 0.25°  |  {meta.get('location','Brasil')}  |  F{int(meta.get('fh', 0)):03d}  |  VALID {meta.get('valid','')}")
    header.setStyleSheet('color:white;background:#000;font:700 16px Consolas;padding:8px;border-bottom:1px solid #444;')
    layout.addWidget(header)
    top = QWidget()
    top_layout = QHBoxLayout(top)
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(6)
    skew = plotSkewT(plot_omega=True)
    hodo = plotHodo()
    skew.setMinimumSize(1000, 650)
    hodo.setMinimumSize(520, 650)
    top_layout.addWidget(skew, 2)
    top_layout.addWidget(hodo, 1)
    layout.addWidget(top, 1)
    bottom = QLabel('ECMWF IFS • SHARPpy • Sideral Meteorologia')
    bottom.setStyleSheet('color:white;background:#050505;font:11px Consolas;padding:6px;border:1px solid #333;')
    layout.addWidget(bottom)
    root.show()
    app.processEvents()
    activate(skew, pc, prof)
    activate(hodo, pc, prof)
    app.processEvents()
    skew.grab().save(str(out_dir / 'skewt.png'), 'PNG')
    hodo.grab().save(str(out_dir / 'hodograph.png'), 'PNG')
    root.grab().save(str(out_dir / 'full.png'), 'PNG')
    root.close()
    app.processEvents()
