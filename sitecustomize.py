"""Sideral runtime compatibility for Qt/PyQt versions used by SHARPpy.

SHARPpy's native renderer was written against older Qt bindings and can pass
floating-point geometry values to QRect. Current PyQt5 requires integer
coordinates/sizes. Python automatically imports sitecustomize during startup,
so this keeps the compatibility fix outside SHARPpy itself.
"""

from numbers import Real


def _qt_int(value):
    if isinstance(value, Real):
        return int(round(float(value)))
    return value


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
    # The compatibility module must never prevent unrelated Python commands
    # from starting when PyQt5 is not installed.
    pass
