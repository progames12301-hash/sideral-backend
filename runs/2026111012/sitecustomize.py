"""Optional Qt/PyQt compatibility for SHARPpy.

PyQt5 is a native stack and must not be imported during normal Python startup.
Call enable_qt_compat() explicitly from the SHARPpy rendering process.
"""
from numbers import Real


def _qt_int(value):
    if isinstance(value, Real):
        return int(round(float(value)))
    return value


def enable_qt_compat():
    try:
        from PyQt5 import QtCore

        if getattr(QtCore, "_SIDERAL_COMPAT_ENABLED", False):
            return

        _QRect = QtCore.QRect
        _QSize = QtCore.QSize
        _QPoint = QtCore.QPoint

        def _compat_qrect(*args):
            return _QRect(*tuple(_qt_int(x) for x in args))

        def _compat_qsize(*args):
            return _QSize(*tuple(_qt_int(x) for x in args))

        def _compat_qpoint(*args):
            return _QPoint(*tuple(_qt_int(x) for x in args))

        QtCore.QRect = _compat_qrect
        QtCore.QSize = _compat_qsize
        QtCore.QPoint = _compat_qpoint
        QtCore._SIDERAL_COMPAT_ENABLED = True
    except Exception:
        # Compatibility must never prevent unrelated Python processes.
        pass


# Kept for CI jobs that explicitly opt in before importing SHARPpy.
import os
if str(os.environ.get("SIDERAL_QT_COMPAT", "")).strip().lower() in {"1", "true", "yes", "on"}:
    enable_qt_compat()
