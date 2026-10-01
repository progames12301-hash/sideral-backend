"""Opt-in runtime compatibility for Qt/PyQt used by SHARPpy.

Do not import PyQt5 during normal Python startup. Render's HTTP service and
most repository scripts do not need Qt, while Qt is a native stack that should
only be loaded by the Skew-T rendering jobs that explicitly opt in.
"""
import os
from numbers import Real


def _qt_int(value):
    if isinstance(value, Real):
        return int(round(float(value)))
    return value


if str(os.environ.get("SIDERAL_QT_COMPAT", "")).strip().lower() in {"1", "true", "yes", "on"}:
    try:
        from PyQt5 import QtCore

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
    except Exception:
        # Compatibility must never block unrelated Python processes.
        pass
