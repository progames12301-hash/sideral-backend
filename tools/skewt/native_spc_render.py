import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QPainter
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel
from sharppy.sharptab.prof_collection import ProfCollection
from sharppy.viz.skew import plotSkewT
from sharppy.viz.hodo import plotHodo

class SideralBrand(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(270)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setStyleSheet('background:#000;')
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QColor('#fff'))
        p.setFont(QFont('Arial', QFont.Bold, 30))
        p.drawText(4, 34, 'SIDERAL')
        p.setPen(QColor('#e84b3c'))
        p.setFont(QFont('Arial', QFont.Bold, 42))
        p.drawText(132, 38, 'X')
        p.end()

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

def set_prof(widget, prof):
    try:
        widget.setProf(prof)
    except Exception:
        pass

def render_native_spc(prof, out_dir: Path, meta: dict):
    """Render the actual SHARPpy widgets with PyQt5 in headless mode."""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
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
        f"SIDERAL SKEW-T  |  ECMWF IFS 0.25°  |  {meta.get('location','Brasil')}  |  "
        f"F{int(meta.get('fh', 0)):03d}  |  VALID {meta.get('valid','')}"
    )
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
