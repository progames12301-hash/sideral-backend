import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')

from pathlib import Path
from numbers import Real

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel
from sharppy.sharptab.prof_collection import ProfCollection
from sharppy.viz import skew as _sharppy_skew
from sharppy.viz.skew import plotSkewT
from sharppy.viz.hodo import plotHodo


def _qt_int(value):
    """Convert Python/NumPy numeric scalars to a Qt-compatible int."""
    if isinstance(value, Real):
        return int(round(float(value)))
    return value


def _qt_args(args):
    return tuple(_qt_int(x) for x in args)


# SHARPpy versions commonly deployed on Render predate current PyQt5's
# stricter overload resolution. Keep the compatibility shim local to the
# SHARPpy modules instead of modifying site-packages.
_original_qfont = _sharppy_skew.QtGui.QFont

def _compat_qfont(*args, **kwargs):
    if len(args) >= 2:
        args = (args[0], _qt_int(args[1]), *args[2:])
    return _original_qfont(*args, **kwargs)

_sharppy_skew.QtGui.QFont = _compat_qfont

# Patch the QPainter primitives used by SHARPpy's skew/hodograph widgets.
# PyQt5 rejects NumPy float scalars even where the old Qt bindings accepted
# them. Preserve QLine/QLineF/QPoint/QPointF objects and normalize numeric
# overloads only.
_orig_draw_line = QPainter.drawLine
_orig_draw_polyline = QPainter.drawPolyline
_orig_draw_point = QPainter.drawPoint
_orig_draw_rect = QPainter.drawRect
_orig_draw_ellipse = QPainter.drawEllipse
_orig_draw_arc = QPainter.drawArc
_orig_draw_text = QPainter.drawText


def _draw_line(self, *args):
    return _orig_draw_line(self, *_qt_args(args))


def _draw_polyline(self, *args):
    return _orig_draw_polyline(self, *_qt_args(args))


def _draw_point(self, *args):
    return _orig_draw_point(self, *_qt_args(args))


def _draw_rect(self, *args):
    return _orig_draw_rect(self, *_qt_args(args))


def _draw_ellipse(self, *args):
    return _orig_draw_ellipse(self, *_qt_args(args))


def _draw_arc(self, *args):
    return _orig_draw_arc(self, *_qt_args(args))


def _draw_text(self, *args):
    # drawText has both geometry and string overloads. Only normalize numeric
    # arguments; QString/text arguments remain untouched.
    return _orig_draw_text(self, *_qt_args(args))

QPainter.drawLine = _draw_line
QPainter.drawPolyline = _draw_polyline
QPainter.drawPoint = _draw_point
QPainter.drawRect = _draw_rect
QPainter.drawEllipse = _draw_ellipse
QPainter.drawArc = _draw_arc
QPainter.drawText = _draw_text


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

    pc = ProfCollection(
        {'ECMWF IFS': [prof]},
        [prof.date],
        highlight='ECMWF IFS',
        location=meta.get('location', 'Brasil'),
    )

    root = QWidget()
    root.setStyleSheet('background:#000;color:#fff;')
    root.resize(1600, 980)

    layout = QVBoxLayout(root)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)

    header = QLabel(
        f"SIDERAL SKEW-T  |  ECMWF IFS 0.25°  |  "
        f"{meta.get('location','Brasil')}  |  "
        f"F{int(meta.get('fh', 0)):03d}  |  VALID {meta.get('valid','')}"
    )
    header.setStyleSheet(
        'color:white;background:#000;font:700 16px Consolas;'
        'padding:8px;border-bottom:1px solid #444;'
    )
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
    bottom.setStyleSheet(
        'color:white;background:#050505;font:11px Consolas;'
        'padding:6px;border:1px solid #333;'
    )
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
