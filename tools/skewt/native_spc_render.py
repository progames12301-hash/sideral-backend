import os
from pathlib import Path
from PySide2.QtWidgets import QApplication, QWidget, QGridLayout, QFrame
from sharppy.sharptab.prof_collection import ProfCollection
from sharppy.viz import (
    plotSkewT, plotHodo, plotText, plotKinematics, plotSpeed,
    plotAdvection, plotSlinky, plotThetae, plotWinds, plotWatch,
    plotAnalogues, plotSTP,
)


def render_native_spc(prof, out_dir, meta):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance() or QApplication([])
    pc = ProfCollection({'ECMWF IFS': [prof]}, [prof.date], highlight='ECMWF IFS', location=meta['location'])

    root = QWidget()
    root.setStyleSheet('QWidget { background-color: #000000; color: #ffffff; }')
    root.resize(1180, 800)
    grid = QGridLayout(root)
    grid.setContentsMargins(1, 1, 1, 1)
    grid.setHorizontalSpacing(0)
    grid.setVerticalSpacing(2)

    right = QFrame()
    rg = QGridLayout(right)
    rg.setContentsMargins(0, 0, 0, 0)
    rg.setHorizontalSpacing(0)
    rg.setVerticalSpacing(0)

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

    # Native SHARPpy SPC-style arrangement: Skew-T at left, native
    # meteorological insets at right, and native parameter panels below.
    rg.addWidget(speed, 0, 0, 11, 3)
    rg.addWidget(advection, 0, 3, 11, 2)
    rg.addWidget(hodo, 0, 5, 8, 24)
    rg.addWidget(slinky, 8, 5, 3, 6)
    rg.addWidget(thetae, 8, 11, 3, 6)
    rg.addWidget(winds, 8, 17, 3, 6)
    rg.addWidget(watch, 8, 23, 3, 6)

    text = QFrame()
    text.setStyleSheet('QFrame { background-color: #000000; border: 2px solid #3399CC; }')
    tg = QGridLayout(text)
    tg.setContentsMargins(0, 0, 0, 0)
    tg.setHorizontalSpacing(0)
    tg.addWidget(convective, 0, 0)
    tg.addWidget(kinematic, 0, 1)
    tg.addWidget(left_inset, 0, 2)
    tg.addWidget(right_inset, 0, 3)

    grid.addWidget(skew, 0, 0, 3, 1)
    grid.addWidget(right, 0, 1, 3, 1)
    grid.addWidget(text, 3, 0, 1, 2)

    root.show()
    app.processEvents()
    skew.addProfileCollection(pc)
    hodo.addProfileCollection(pc)
    app.processEvents()

    for widget in (speed, advection, slinky, thetae, winds, watch, convective,
                   kinematic, left_inset, right_inset):
        try:
            widget.setProf(prof)
        except Exception:
            pass
    app.processEvents()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    skew.grab().save(str(out_dir / 'skewt.png'), 'PNG')
    hodo.grab().save(str(out_dir / 'hodograph.png'), 'PNG')
    root.grab().save(str(out_dir / 'full.png'), 'PNG')
    root.close()
    app.processEvents()
