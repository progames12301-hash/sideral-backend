import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')

from datetime import timedelta
from pathlib import Path
from numbers import Real

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QPainter
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QActionGroup
)
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


def _num(value, digits=0, suffix='--'):
    try:
        import numpy as np
        if value is None or np.ma.is_masked(value):
            return suffix
        value = float(value)
        if not np.isfinite(value):
            return suffix
        return f'{value:.{digits}f}'
    except Exception:
        return suffix


def _mag(prof, attr):
    try:
        import math
        a = getattr(prof, attr)
        return math.hypot(float(a[0]), float(a[1]))
    except Exception:
        return None


def _parcel(pcl, attr, digits=0, unit=''):
    return _num(getattr(pcl, attr, None) if pcl is not None else None, digits) + unit


def _value(prof, names, digits=0, unit=''):
    for name in names:
        value = getattr(prof, name, None)
        if value is not None:
            return _num(value, digits) + unit
    return '--'


def _title(text):
    label = QLabel(text)
    label.setStyleSheet('color:#fff;background:#050505;border:1px solid #333;padding:4px 6px;font:700 10px Consolas;')
    return label


def _table(headers, rows, widths=None):
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.setFocusPolicy(Qt.NoFocus)
    table.setStyleSheet('''
        QTableWidget { background:#030303; color:#fff; border:1px solid #333; gridline-color:#292929;
                       font:600 9px Consolas; }
        QHeaderView::section { background:#090909; color:#fff; border:1px solid #333;
                               padding:3px; font:700 9px Consolas; }
    ''')
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    table.setMinimumHeight(72)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(r, c, item)
    if widths:
        for i, w in enumerate(widths):
            table.setColumnWidth(i, w)
    return table


def _parcel_table(prof):
    p = [getattr(prof, 'sfcpcl', None), getattr(prof, 'mlpcl', None), getattr(prof, 'mupcl', None)]
    names = ['Surface-Based', 'Mixed-Layer', 'Most Unstable']
    rows = []
    for name, pcl in zip(names, p):
        rows.append([
            name,
            _parcel(pcl, 'bplus'),
            _parcel(pcl, 'lclhght'),
            _parcel(pcl, 'lfchght'),
            _parcel(pcl, 'elhght'),
            _parcel(pcl, 'bminus'),
            _parcel(pcl, 'li5', 1),
            _parcel(pcl, 'tmpc', 1, ' °C'),
            _parcel(pcl, 'dwpc', 1, ' °C'),
        ])
    return _table(['PARCEL','CAPE','LCL','LFC','EL','CINH','LI','TEMP','DWPT'], rows)


def _diagnostic_table(prof):
    sb = getattr(prof, 'sfcpcl', None)
    ml = getattr(prof, 'mlpcl', None)
    mu = getattr(prof, 'mupcl', None)
    rows = [
        ['SBCAPE', _parcel(sb,'bplus')+' J/kg', 'MLCAPE', _parcel(ml,'bplus')+' J/kg', 'MUCAPE', _parcel(mu,'bplus')+' J/kg', 'SBCIN', _parcel(sb,'bminus')+' J/kg'],
        ['MLCIN', _parcel(ml,'bminus')+' J/kg', 'MUCIN', _parcel(mu,'bminus')+' J/kg', 'SBLI', _parcel(sb,'li5',1)+' °C', 'MLLI', _parcel(ml,'li5',1)+' °C'],
        ['MULI', _parcel(mu,'li5',1)+' °C', 'PWAT', _value(prof,['pwat','pw','precip_water'],2)+' in', 'K', _value(prof,['k_idx','k_index'],1), 'TT', _value(prof,['totals_totals','tt'],1)],
        ['0–1 km SRH', _value(prof,['srh1km'],0)+' m²/s²', '0–3 km SRH', _value(prof,['srh3km'],0)+' m²/s²', '0–1 km SHEAR', _num(_mag(prof,'sfc_1km_shear'),1)+' kt', '0–3 km SHEAR', _num(_mag(prof,'sfc_3km_shear'),1)+' kt'],
        ['0–6 km SHEAR', _num(_mag(prof,'sfc_6km_shear'),1)+' kt', 'SCP', _value(prof,['scp'],2), 'STP', _value(prof,['stp_cin','stp_fixed'],2), 'SHIP', _value(prof,['ship'],2)],
    ]
    # Flatten to a compact 8-column diagnostic grid.
    flat = []
    for row in rows:
        flat.append(row)
    return _table(['PARAM','VALUE','PARAM','VALUE','PARAM','VALUE','PARAM','VALUE'], flat)


def _shear_table(prof):
    levels = [
        ('Sfc–500 m','sfc_500m_shear','srh500m'),
        ('Sfc–1 km','sfc_1km_shear','srh1km'),
        ('Sfc–3 km','sfc_3km_shear','srh3km'),
        ('Sfc–6 km','sfc_6km_shear','srh6km'),
        ('LCL–EL','effective_shear','effective_srh'),
    ]
    rows=[]
    for name, shear, srh in levels:
        rows.append([name, _num(_mag(prof,shear),1)+' kt', _value(prof,[srh],0)+' m²/s²'])
    eff = _mag(prof,'effective_shear')
    rows.append(['Effective Shear', _num(eff,1)+' kt', _value(prof,['effective_srh'],0)+' m²/s²'])
    return _table(['LAYER','BWD','SRH'], rows)


def _legend():
    frame = QFrame()
    frame.setStyleSheet('QFrame{background:#030303;border:1px solid #333;} QLabel{font:700 9px Consolas;padding:2px;}')
    grid = QGridLayout(frame)
    grid.setContentsMargins(6,4,6,4)
    items = [
        ('#ff3b30','Temperatura'),('#34c759','Ponto de orvalho'),('#00e5ff','Temperatura Virtual'),
        ('#ffffff','Temp. Bulbo Úmido'),('#ff00ff','Parcela Descendente'),('#ffff00','Parcela Efetiva'),
        ('#ffd400','Parcela Most Unstable'),('#ffffff','Wind Barbs')
    ]
    for i,(color,text) in enumerate(items):
        box=QLabel('━━')
        box.setStyleSheet(f'color:{color};background:#030303;font:700 11px Consolas;border:0;')
        grid.addWidget(box,i//4*2,i%4*2)
        grid.addWidget(QLabel(text),i//4*2,i%4*2+1)
    return frame


def render_native_spc(prof, out_dir: Path, meta: dict):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])

    fh = int(meta.get('fh', 0))
    valid = prof.date
    run = valid - timedelta(hours=fh)
    location = meta.get('location', 'Brasil')
    pc = ProfCollection(
        {'ECMWF IFS': [prof]}, [valid], highlight='ECMWF IFS',
        location=location, model='ECMWF IFS', run=run,
        base_time=run, fhour=[f'F{fh:03d}'], observed=False, loc=location,
    )

    root = QWidget()
    root.setStyleSheet('background:#000;color:#fff;')
    root.resize(1600, 1180)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(8,8,8,8)
    layout.setSpacing(4)

    header = QLabel(f'SIDERAL SKEW-T   |   ECMWF IFS 0.25°   |   {location}   |   F{fh:03d}   |   VALID {valid:%Y-%m-%d %HZ}')
    header.setStyleSheet('color:#fff;background:#000;font:700 15px Consolas;padding:5px;border-bottom:1px solid #444;')
    layout.addWidget(header)

    main = QHBoxLayout()
    main.setSpacing(5)
    skew_box = QWidget()
    skew_layout = QVBoxLayout(skew_box)
    skew_layout.setContentsMargins(0,0,0,0)
    skew_layout.setSpacing(3)
    skew_layout.addWidget(_legend())
    skew = plotSkewT(plot_omega=True)
    skew.setMinimumSize(1010, 720)
    skew_layout.addWidget(skew, 1)

    hodo_box = QWidget()
    hodo_layout = QVBoxLayout(hodo_box)
    hodo_layout.setContentsMargins(0,0,0,0)
    hodo_layout.setSpacing(3)
    hodo_layout.addWidget(_title('HODOGRAPH   •   0–3 km SHEAR / SRH'))
    hodo = plotHodo()
    hodo.setMinimumSize(540,720)
    hodo_layout.addWidget(hodo,1)

    main.addWidget(skew_box, 2)
    main.addWidget(hodo_box, 1)
    layout.addLayout(main, 1)

    layout.addWidget(_parcel_table(prof))
    layout.addWidget(_diagnostic_table(prof))

    bottom = QHBoxLayout()
    bottom.setSpacing(4)
    bottom.addWidget(_shear_table(prof), 1)
    bottom.addWidget(_title('PARÂMETROS SHARPpy\nSBCAPE / MLCAPE / MUCAPE • CIN • LCL • LFC • EL\nSRH • SHEAR • SCP • STP • SHIP • PWAT'), 1)
    layout.addLayout(bottom)

    footer = QLabel('ECMWF IFS  •  SHARPpy  •  Sideral Meteorologia  •  valores calculados pelo SHARPpy')
    footer.setStyleSheet('color:#ddd;background:#050505;font:10px Consolas;padding:5px;border:1px solid #333;')
    layout.addWidget(footer)

    root.show()
    app.processEvents()

    parcel_obj = _select_parcel(prof)
    activate(skew, pc, parcel=parcel_obj)
    activate(hodo, pc)
    app.processEvents()
    app.processEvents()

    skew.grab().save(str(out_dir/'skewt.png'),'PNG')
    hodo.grab().save(str(out_dir/'hodograph.png'),'PNG')
    root.grab().save(str(out_dir/'full.png'),'PNG')
    root.close()
    app.processEvents()
    return out_dir/'full.png'
