import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')

from datetime import timedelta
from pathlib import Path
from numbers import Real

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QPainter
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame, QActionGroup
from sharppy.sharptab.prof_collection import ProfCollection
from sharppy.viz import skew as _sharppy_skew
from sharppy.viz.skew import plotSkewT
from sharppy.viz.hodo import plotHodo


def _qt_int(value):
    if isinstance(value, Real):
        return int(round(float(value)))
    return value


def _qt_args(args):
    return tuple(_qt_int(x) for x in args)


_original_qfont = _sharppy_skew.QtGui.QFont

def _compat_qfont(*args, **kwargs):
    if len(args) >= 2:
        args = (args[0], _qt_int(args[1]), *args[2:])
    return _original_qfont(*args, **kwargs)

_sharppy_skew.QtGui.QFont = _compat_qfont

_original_action_group = QActionGroup

class _CompatActionGroup(_original_action_group):
    def __init__(self, parent=None, *args, **kwargs):
        exclusive = kwargs.pop('exclusive', None)
        super().__init__(parent, *args, **kwargs)
        if exclusive is not None:
            self.setExclusive(bool(exclusive))

_sharppy_skew.QtWidgets.QActionGroup = _CompatActionGroup

_orig_draw_line = QPainter.drawLine
_orig_draw_polyline = QPainter.drawPolyline
_orig_draw_point = QPainter.drawPoint
_orig_draw_rect = QPainter.drawRect
_orig_draw_ellipse = QPainter.drawEllipse
_orig_draw_arc = QPainter.drawArc
_orig_draw_text = QPainter.drawText


def _draw_line(self, *args): return _orig_draw_line(self, *_qt_args(args))
def _draw_polyline(self, *args): return _orig_draw_polyline(self, *_qt_args(args))
def _draw_point(self, *args): return _orig_draw_point(self, *_qt_args(args))
def _draw_rect(self, *args): return _orig_draw_rect(self, *_qt_args(args))
def _draw_ellipse(self, *args): return _orig_draw_ellipse(self, *_qt_args(args))
def _draw_arc(self, *args): return _orig_draw_arc(self, *_qt_args(args))
def _draw_text(self, *args): return _orig_draw_text(self, *_qt_args(args))

QPainter.drawLine = _draw_line
QPainter.drawPolyline = _draw_polyline
QPainter.drawPoint = _draw_point
QPainter.drawRect = _draw_rect
QPainter.drawEllipse = _draw_ellipse
QPainter.drawArc = _draw_arc
QPainter.drawText = _draw_text


def _select_parcel(prof):
    """Choose a valid SHARPpy parcel for the native renderer.

    SHARPpy's plotData() expects widget.pcl to be populated before
    setActiveCollection() triggers the first draw. Prefer MU, then ML, then
    surface based so a missing most-unstable parcel does not abort the run.
    """
    for attr in ('mupcl', 'mlpcl', 'sfcpcl'):
        pcl = getattr(prof, attr, None)
        if pcl is not None:
            return pcl
    return None


def activate(widget, pc, parcel=None):
    widget.addProfileCollection(pc)
    if parcel is not None and hasattr(widget, 'setParcel'):
        widget.setParcel(parcel)
    elif parcel is not None:
        widget.pcl = parcel
    widget.setActiveCollection(0, update_gui=True)


def _fmt(value, digits=1, suffix=''):
    try:
        import numpy as np
        if np.ma.is_masked(value):
            return '--'
        value = float(value)
        if not np.isfinite(value):
            return '--'
        return f'{value:.{digits}f}{suffix}'
    except Exception:
        return '--'


def _parcel_rows(prof):
    rows = []
    for name, pcl in [('SFC', getattr(prof, 'sfcpcl', None)), ('ML', getattr(prof, 'mlpcl', None)), ('MU', getattr(prof, 'mupcl', None))]:
        if pcl is None:
            continue
        rows.append((name,
                     f'CAPE {_fmt(getattr(pcl, "bplus", None), 0)} J/kg',
                     f'CIN {_fmt(getattr(pcl, "bminus", None), 0)} J/kg',
                     f'LCL {_fmt(getattr(pcl, "lclhght", None), 0)} m',
                     f'LFC {_fmt(getattr(pcl, "lfchght", None), 0)} m',
                     f'EL {_fmt(getattr(pcl, "elhght", None), 0)} m'))
    return rows


def _metric_label(text, strong=False):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet('color:#fff;background:#080808;border:1px solid #292929;padding:7px;font:%s 11px Consolas;' % ('700' if strong else '600'))
    return label


def _build_diagnostics(prof):
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(4)
    grid.setVerticalSpacing(4)
    items = [
        ('SBCAPE', _fmt(getattr(prof.sfcpcl, 'bplus', None), 0, ' J/kg')),
        ('MLCAPE', _fmt(getattr(prof.mlpcl, 'bplus', None), 0, ' J/kg')),
        ('MUCAPE', _fmt(getattr(prof.mupcl, 'bplus', None), 0, ' J/kg')),
        ('SBCIN', _fmt(getattr(prof.sfcpcl, 'bminus', None), 0, ' J/kg')),
        ('MLCIN', _fmt(getattr(prof.mlpcl, 'bminus', None), 0, ' J/kg')),
        ('MUCIN', _fmt(getattr(prof.mupcl, 'bminus', None), 0, ' J/kg')),
        ('SBLI', _fmt(getattr(prof.sfcpcl, 'li5', None), 1, ' °C')),
        ('MLLI', _fmt(getattr(prof.mlpcl, 'li5', None), 1, ' °C')),
        ('MULI', _fmt(getattr(prof.mupcl, 'li5', None), 1, ' °C')),
        ('K', _fmt(getattr(prof, 'k_idx', None), 1)),
        ('TT', _fmt(getattr(prof, 'totals_totals', None), 1)),
        ('PWAT', _fmt(getattr(prof, 'pwat', None), 2, ' in')),
        ('0–1 km SRH', _fmt(getattr(prof, 'srh1km', [None])[0], 0, ' m²/s²')),
        ('0–3 km SRH', _fmt(getattr(prof, 'srh3km', [None])[0], 0, ' m²/s²')),
        ('0–1 km SHEAR', _fmt(_mag(prof, 'sfc_1km_shear'), 1, ' kt')),
        ('0–3 km SHEAR', _fmt(_mag(prof, 'sfc_3km_shear'), 1, ' kt')),
        ('0–6 km SHEAR', _fmt(_mag(prof, 'sfc_6km_shear'), 1, ' kt')),
        ('SCP', _fmt(getattr(prof, 'scp', None), 2)),
        ('STP', _fmt(getattr(prof, 'stp_cin', None), 2)),
        ('SHIP', _fmt(getattr(prof, 'ship', None), 2)),
        ('LCL', _fmt(getattr(prof.mlpcl, 'lclhght', None), 0, ' m')),
        ('LFC', _fmt(getattr(prof.mlpcl, 'lfchght', None), 0, ' m')),
        ('EL', _fmt(getattr(prof.mlpcl, 'elhght', None), 0, ' m')),
        ('Critical Angle', _fmt(getattr(prof, 'critical_angle', None), 0, '°')),
    ]
    for i, (name, value) in enumerate(items):
        grid.addWidget(_metric_label(f'{name}\n{value}', strong=True), i // 8, i % 8)
    return grid


def _mag(prof, attr):
    try:
        import math
        a = getattr(prof, attr)
        return math.hypot(float(a[0]), float(a[1]))
    except Exception:
        return None


def render_native_spc(prof, out_dir: Path, meta: dict):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])

    fh = int(meta.get('fh', 0))
    valid = prof.date
    run = valid - timedelta(hours=fh)
    pc = ProfCollection(
        {'ECMWF IFS': [prof]}, [valid], highlight='ECMWF IFS',
        location=meta.get('location', 'Brasil'), model='ECMWF IFS', run=run,
        base_time=run, fhour=[f'F{fh:03d}'], observed=False,
        loc=meta.get('location', 'Brasil'),
    )

    root = QWidget()
    root.setStyleSheet('background:#000;color:#fff;')
    root.resize(1800, 1180)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(5)

    header = QLabel(f"SIDERAL SKEW-T  |  ECMWF IFS 0.25°  |  {meta.get('location','Brasil')}  |  F{fh:03d}  |  VALID {valid:%Y-%m-%d %HZ}")
    header.setStyleSheet('color:white;background:#000;font:700 16px Consolas;padding:8px;border-bottom:1px solid #444;')
    layout.addWidget(header)

    top = QWidget()
    top_layout = QHBoxLayout(top)
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(6)
    skew = plotSkewT(plot_omega=True)
    hodo = plotHodo()
    skew.setMinimumSize(1120, 760)
    hodo.setMinimumSize(560, 760)
    top_layout.addWidget(skew, 2)
    top_layout.addWidget(hodo, 1)
    layout.addWidget(top, 1)

    diag_frame = QFrame()
    diag_frame.setStyleSheet('QFrame{background:#000;border-top:1px solid #333;}')
    diag_frame.setLayout(_build_diagnostics(prof))
    layout.addWidget(diag_frame)

    parcel_text = '   '.join(' | '.join(row) for row in _parcel_rows(prof))
    parcel = QLabel(parcel_text)
    parcel.setWordWrap(True)
    parcel.setStyleSheet('color:#ddd;background:#050505;font:600 10px Consolas;padding:6px;border:1px solid #333;')
    layout.addWidget(parcel)

    footer = QLabel('ECMWF IFS • SHARPpy • Sideral Meteorologia • valores calculados pelo SHARPpy')
    footer.setStyleSheet('color:white;background:#050505;font:11px Consolas;padding:6px;border:1px solid #333;')
    layout.addWidget(footer)

    root.show()
    app.processEvents()

    parcel_obj = _select_parcel(prof)
    activate(skew, pc, parcel=parcel_obj)
    activate(hodo, pc)
    app.processEvents()
    app.processEvents()

    skew.grab().save(str(out_dir / 'skewt.png'), 'PNG')
    hodo.grab().save(str(out_dir / 'hodograph.png'), 'PNG')
    root.grab().save(str(out_dir / 'full.png'), 'PNG')
    root.close()
    app.processEvents()
    return out_dir / 'full.png'
