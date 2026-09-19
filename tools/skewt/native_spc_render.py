import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')

from pathlib import Path
from PySide2.QtCore import Qt
from PySide2.QtGui import QFont, QColor, QPainter
from PySide2.QtWidgets import QApplication, QWidget, QGridLayout, QFrame, QLabel, QHBoxLayout
from sharppy.sharptab.prof_collection import ProfCollection
from sharppy.viz import (
    plotSkewT, plotHodo, plotText, plotKinematics, plotSpeed,
    plotAdvection, plotSlinky, plotThetae, plotWinds, plotWatch,
    plotAnalogues, plotSTP,
)


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
        p.setPen(QColor('#ffffff'))
        p.setFont(QFont('Arial', QFont.Bold, 30))
        p.drawText(4, 34, 'SIDERAL')
        p.setPen(QColor('#e84b3c'))
        p.setFont(QFont('Arial', QFont.Bold, 42))
        p.drawText(132, 38, 'X')
        p.end()


def _set_profile(widget, pc, prof, idx=0):
    # Follow SHARPpy SPCWindow's normal update sequence: add the collection,
    # activate it, then set the actual profile. This is what makes the native
    # temperature/dewpoint traces, wind barbs and hodograph paint correctly.
    widget.addProfileCollection(pc)
    try:
        widget.setActiveCollection(idx, update_gui=False)
    except TypeError:
        try:
            widget.setActiveCollection(idx)
        except Exception:
            pass
    except Exception:
        pass
    try:
        widget.setProf(prof)
    except Exception:
        pass


def render_native_spc(prof, out_dir, meta):
    app = QApplication.instance() or QApplication([])
    pc = ProfCollection(
        {'ECMWF IFS': [prof]},
        [prof.date],
        highlight='ECMWF IFS',
        location=meta['location'],
    )

    root = QWidget()
    root.setStyleSheet('QWidget { background:#000000; color:#ffffff; }')
    root.resize(1600, 1000)

    grid = QGridLayout(root)
    grid.setContentsMargins(7, 5, 7, 5)
    grid.setHorizontalSpacing(5)
    grid.setVerticalSpacing(4)
    grid.setColumnStretch(0, 3)
    grid.setColumnStretch(1, 2)
    grid.setRowStretch(1, 6)
    grid.setRowStretch(2, 2)

    header = QFrame()
    header.setStyleSheet('QFrame{background:#000;border-bottom:1px solid #333;}')
    hg = QHBoxLayout(header)
    hg.setContentsMargins(0, 0, 0, 2)
    brand = SideralBrand()
    hg.addWidget(brand)
    title = QLabel(
        f'ECMWF IFS 0.25°  |  {meta["location"]}  |  F{meta["fh"]:03d}  |  VALID {meta["valid"]}'
    )
    title.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    title.setStyleSheet('color:#fff;font:700 15px Consolas;background:#000;padding-right:5px;')
    hg.addWidget(title, 1)
    grid.addWidget(header, 0, 0, 1, 2)

    skew = plotSkewT(plot_omega=True)
    hodo = plotHodo()
    speed = plotSpeed()
    advection = plotAdvection()
    slinky = plotSlinky()
    thetae = plotThetae()
    winds = plotWinds()
    watch = plotWatch()
    convective = plotText(['SFC', 'ML', 'FCST', 'MU'])
    kinematic = plotKinematics()
    left_inset = plotAnalogues()
    right_inset = plotSTP()

    skew.setMinimumSize(980, 650)
    hodo.setMinimumSize(500, 650)

    right = QFrame()
    rg = QGridLayout(right)
    rg.setContentsMargins(0, 0, 0, 0)
    rg.setHorizontalSpacing(2)
    rg.setVerticalSpacing(2)
    rg.addWidget(hodo, 0, 0, 8, 12)
    rg.addWidget(speed, 8, 0, 4, 3)
    rg.addWidget(advection, 8, 3, 4, 3)
    rg.addWidget(slinky, 8, 6, 4, 2)
    rg.addWidget(thetae, 8, 8, 4, 2)
    rg.addWidget(winds, 8, 10, 4, 2)

    grid.addWidget(skew, 1, 0)
    grid.addWidget(right, 1, 1)

    bottom = QFrame()
    bottom.setStyleSheet('QFrame{background:#000;border:2px solid #333;}')
    bg = QGridLayout(bottom)
    bg.setContentsMargins(2, 2, 2, 2)
    bg.setHorizontalSpacing(3)
    bg.addWidget(convective, 0, 0, 1, 2)
    bg.addWidget(kinematic, 0, 2, 1, 2)
    bg.addWidget(left_inset, 0, 4, 1, 1)
    bg.addWidget(right_inset, 0, 5, 1, 1)
    grid.addWidget(bottom, 2, 0, 1, 2)

    footer = QLabel('F%03d  •  POWERED BY SHARPpy / SIDERALMETEOROLOGIA.COM.BR' % meta['fh'])
    footer.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    footer.setStyleSheet('color:#ddd;background:#000;font:700 11px Consolas;padding:3px;border-top:1px solid #222;')
    grid.addWidget(footer, 3, 0, 1, 2)

    root.show()
    app.processEvents()

    _set_profile(skew, pc, prof)
    _set_profile(hodo, pc, prof)
    for widget in (speed, advection, slinky, thetae, winds, watch,
                   convective, kinematic, left_inset, right_inset):
        try:
            widget.setProf(prof)
        except Exception:
            pass

    app.processEvents()
    root.resize(1600, 1000)
    root.adjustSize()
    root.update()
    for widget in (skew, hodo, speed, advection, slinky, thetae, winds, watch,
                   convective, kinematic, left_inset, right_inset):
        widget.update()
    app.processEvents()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    skew.grab().save(str(out_dir / 'skewt.png'), 'PNG')
    hodo.grab().save(str(out_dir / 'hodograph.png'), 'PNG')
    root.grab().save(str(out_dir / 'full.png'), 'PNG')

    root.close()
    app.processEvents()
