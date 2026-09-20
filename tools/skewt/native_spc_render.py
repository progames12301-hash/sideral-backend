import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')
from pathlib import Path
from PySide2.QtCore import Qt
from PySide2.QtGui import QFont, QColor, QPainter
from PySide2.QtWidgets import QApplication, QWidget, QGridLayout, QFrame, QLabel, QHBoxLayout
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

def render_native_spc(prof, out_dir, meta):
    app=QApplication.instance() or QApplication([])
    pc=ProfCollection({'ECMWF IFS':[prof]}, [prof.date], highlight='ECMWF IFS', location=meta['location'])
    root=QWidget(); root.setStyleSheet('QWidget{background:#000;color:#fff;}'); root.resize(1800,1200)
    grid=QGridLayout(root); grid.setContentsMargins(7,5,7,5); grid.setHorizontalSpacing(5); grid.setVerticalSpacing(4); grid.setColumnStretch(0,3); grid.setColumnStretch(1,2); grid.setRowStretch(1,8); grid.setRowStretch(2,3)
    header=QFrame(); header.setStyleSheet('QFrame{background:#000;border-bottom:1px solid #333;}'); hg=QHBoxLayout(header); hg.setContentsMargins(0,0,0,2); hg.addWidget(SideralBrand())
    title=QLabel(f'ECMWF IFS 0.25°  |  {meta["location"]}  |  F{meta["fh"]:03d}  |  INIT {meta.get("run","")}  |  VALID {meta["valid"]}'); title.setAlignment(Qt.AlignRight|Qt.AlignVCenter); title.setStyleSheet('color:#fff;font:700 15px Consolas;background:#000;padding-right:5px;'); hg.addWidget(title,1); grid.addWidget(header,0,0,1,2)
    skew=plotSkewT(plot_omega=True); hodo=plotHodo(); speed=plotSpeed(); advection=plotAdvection(); slinky=plotSlinky(); thetae=plotThetae(); winds=plotWinds(); watch=plotWatch(); convective=plotText(['SFC','ML','FCST','MU']); kinematic=plotKinematics(); left_inset=plotAnalogues(); right_inset=plotSTP()
    skew.setMinimumSize(1050,720); hodo.setMinimumSize(520,520)
    for w in (speed,advection,slinky,thetae,winds): w.setMinimumSize(120,120)
    for w in (convective,kinematic,left_inset,right_inset): w.setMinimumSize(180,180)
    right=QFrame(); rg=QGridLayout(right); rg.setContentsMargins(0,0,0,0); rg.setHorizontalSpacing(3); rg.setVerticalSpacing(3); rg.setRowStretch(0,5); rg.setRowStretch(1,2); rg.addWidget(hodo,0,0,1,12); rg.addWidget(speed,1,0); rg.addWidget(advection,1,1); rg.addWidget(slinky,1,2); rg.addWidget(thetae,1,3); rg.addWidget(winds,1,4)
    grid.addWidget(skew,1,0); grid.addWidget(right,1,1)
    bottom=QFrame(); bottom.setStyleSheet('QFrame{background:#000;border:2px solid #333;}'); bg=QGridLayout(bottom); bg.setContentsMargins(2,2,2,2); bg.setHorizontalSpacing(3); bg.addWidget(convective,0,0,1,2); bg.addWidget(kinematic,0,2,1,2); bg.addWidget(left_inset,0,4,1,1); bg.addWidget(right_inset,0,5,1,1); grid.addWidget(bottom,2,0,1,2)
    footer=QLabel(f'F{meta["fh"]:03d}  •  POWERED BY SHARPpy / SIDERALMETEOROLOGIA.COM.BR'); footer.setAlignment(Qt.AlignRight|Qt.AlignVCenter); footer.setStyleSheet('color:#ddd;background:#000;font:700 11px Consolas;padding:3px;border-top:1px solid #222;'); grid.addWidget(footer,3,0,1,2)
    activate(skew,pc,prof); activate(hodo,pc,prof)
    for w in (speed,advection,slinky,thetae,winds,watch,convective,kinematic,left_inset,right_inset): set_prof(w,prof)
    root.show(); root.resize(1800,1200); root.adjustSize(); app.processEvents(); root.update()
    for w in (skew,hodo,speed,advection,slinky,thetae,winds,watch,convective,kinematic,left_inset,right_inset): w.update(); w.repaint()
    app.processEvents(); app.processEvents()
    out_dir=Path(out_dir); out_dir.mkdir(parents=True,exist_ok=True); skew.grab().save(str(out_dir/'skewt.png'),'PNG'); hodo.grab().save(str(out_dir/'hodograph.png'),'PNG'); root.grab().save(str(out_dir/'full.png'),'PNG'); root.close(); app.processEvents()
