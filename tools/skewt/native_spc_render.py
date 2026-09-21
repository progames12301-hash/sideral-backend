import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QPainter
from PyQt5.QtWidgets import QApplication, QWidget, QGridLayout, QFrame, QLabel, QHBoxLayout
from sharppy.sharptab.prof_collection import ProfCollection
from sharppy.viz import plotSkewT, plotHodo, plotText, plotKinematics, plotSpeed, plotAdvection, plotSlinky, plotThetae, plotWinds, plotWatch, plotAnalogues, plotSTP

class SideralBrand(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent); self.setMinimumWidth(270); self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter); self.setStyleSheet('background:#000;')
    def paintEvent(self, event):
        super().paintEvent(event); p=QPainter(self); p.setRenderHint(QPainter.Antialiasing, True); p.setPen(QColor('#fff')); p.setFont(QFont('Arial', QFont.Bold, 30)); p.drawText(4,34,'SIDERAL'); p.setPen(QColor('#e84b3c')); p.setFont(QFont('Arial', QFont.Bold,42)); p.drawText(132,38,'X'); p.end()

def activate(widget, pc, prof):
    widget.addProfileCollection(pc)
    try:
        widget.setActiveCollection(0)
        return
    except Exception:
        pass
    try: widget.setProf(prof)
    except Exception: pass

def set_prof(widget, prof):
    try: widget.setProf(prof)
    except Exception: pass
