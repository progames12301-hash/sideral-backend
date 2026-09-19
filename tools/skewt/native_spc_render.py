import os

# Must be set before importing PySide2 so the GitHub runner never tries to use
# a real desktop display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

from pathlib import Path
from PySide2.QtWidgets import QApplication, QWidget, QGridLayout, QFrame
from sharppy.sharptab.prof_collection import ProfCollection
from sharppy.viz import (
    plotSkewT, plotHodo, plotText, plotKinematics, plotSpeed,
    plotAdvection, plotSlinky, plotThetae, plotWinds, plotWatch,
    plotAnalogues, plotSTP,
)


def _activate(widget, pc, prof):
    """Attach the collection and explicitly activate profile 0.

    SHARPpy's addProfileCollection() only stores the collection. The normal
    SPCWindow subsequently calls setActiveCollection(); our headless renderer
    bypasses SPCWindow, so that activation must be done explicitly or the
    native trace/hodograph/barbs remain blank.
    """
    widget.addProfileCollection(pc)
    try:
        widget.setActiveCollection(0)
    except Exception:
        try:
            widget.setProf(prof)
        except Exception:
            pass


def render_native_spc(prof, out_dir, meta):
    app = QApplication.instance() or QApplication([])

    pc = ProfCollection(
        {"ECMWF IFS": [prof]},
        [prof.date],
        highlight="ECMWF IFS",
        location=meta["location"],
    )

    root = QWidget()
    root.setStyleSheet("QWidget { background-color: #000000; color: #ffffff; }")
    # Give the native SHARPpy panels enough room so titles and traces are not
    # clipped. The web page does not generate this image; this is the final
    # server-side product.
    root.resize(1600, 1000)

    grid = QGridLayout(root)
    grid.setContentsMargins(6, 6, 6, 6)
    grid.setHorizontalSpacing(5)
    grid.setVerticalSpacing(5)
    grid.setColumnStretch(0, 3)
    grid.setColumnStretch(1, 2)
    grid.setRowStretch(0, 5)
    grid.setRowStretch(1, 1)

    skew = plotSkewT(plot_omega=True)
    hodo = plotHodo()
    speed = plotSpeed()
    advection = plotAdvection()
    slinky = plotSlinky()
    thetae = plotThetae()
    winds = plotWinds()
    watch = plotWatch()
    convective = plotText(["SFC", "ML", "FCST", "MU"])
    kinematic = plotKinematics()
    left_inset = plotAnalogues()
    right_inset = plotSTP()

    skew.setMinimumSize(850, 700)
    hodo.setMinimumSize(500, 600)

    right = QFrame()
    rg = QGridLayout(right)
    rg.setContentsMargins(0, 0, 0, 0)
    rg.setHorizontalSpacing(2)
    rg.setVerticalSpacing(2)
    rg.addWidget(speed, 0, 0, 11, 3)
    rg.addWidget(advection, 0, 3, 11, 2)
    rg.addWidget(hodo, 0, 5, 8, 24)
    rg.addWidget(slinky, 8, 5, 3, 6)
    rg.addWidget(thetae, 8, 11, 3, 6)
    rg.addWidget(winds, 8, 17, 3, 6)
    rg.addWidget(watch, 8, 23, 3, 6)

    text = QFrame()
    text.setStyleSheet(
        "QFrame { background-color: #000000; border: 2px solid #3399CC; }"
    )
    tg = QGridLayout(text)
    tg.setContentsMargins(2, 2, 2, 2)
    tg.setHorizontalSpacing(2)
    tg.addWidget(convective, 0, 0)
    tg.addWidget(kinematic, 0, 1)
    tg.addWidget(left_inset, 0, 2)
    tg.addWidget(right_inset, 0, 3)

    grid.addWidget(skew, 0, 0)
    grid.addWidget(right, 0, 1)
    grid.addWidget(text, 1, 0, 1, 2)

    root.show()
    app.processEvents()

    # Critical fix: activate the collection. In normal SHARPpy SPCWindow this
    # happens inside updateProfs(); our standalone renderer must do it itself.
    _activate(skew, pc, prof)
    _activate(hodo, pc, prof)

    # The remaining native panels are profile-driven and need their profile set
    # explicitly because SPCWindow is not being used here.
    for widget in (
        speed, advection, slinky, thetae, winds, watch,
        convective, kinematic, left_inset, right_inset,
    ):
        try:
            widget.setProf(prof)
        except Exception:
            pass

    app.processEvents()
    root.resize(1600, 1000)
    root.update()
    skew.update()
    hodo.update()
    app.processEvents()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    skew.grab().save(str(out_dir / "skewt.png"), "PNG")
    hodo.grab().save(str(out_dir / "hodograph.png"), "PNG")
    root.grab().save(str(out_dir / "full.png"), "PNG")

    root.close()
    app.processEvents()
