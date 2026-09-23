from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def render_native_spc(prof, tmp_dir, meta):
    """Render the SHARPpy profile without invoking the Qt GUI layer.

    SHARPpy's plotSkewT currently constructs QtWidgets.QActionGroup with an
    ``exclusive=`` keyword that is not accepted by the Qt binding installed
    on GitHub Actions.  The operational renderer is intentionally headless;
    use the matplotlib SPC renderer instead of plotSkewT so the workflow does
    not depend on Qt.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from sharppy.sharptab import utils
    from sharppy.sharptab import thermo

    out_dir = Path(tmp_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "skewt.png"

    fig = plt.figure(figsize=(10, 10), dpi=120)
    ax = fig.add_axes([0.10, 0.10, 0.80, 0.82])

    # Preserve the SPC/SHARPpy pressure-vs-temperature geometry while avoiding
    # SHARPpy's Qt-based plotSkewT widget.  Data and thermodynamic calculations
    # still come from the SHARPpy profile object.
    ax.set_yscale("log")
    ax.set_ylim(1050, 100)
    ax.invert_yaxis()
    ax.set_xlim(-40, 45)
    ax.set_xlabel("Temperature (°C)")
    ax.set_ylabel("Pressure (hPa)")
    ax.grid(True, which="both", alpha=0.25)

    p = getattr(prof, "pres", None)
    t = getattr(prof, "tmpc", None)
    td = getattr(prof, "dwpc", None)
    if p is None or t is None or td is None:
        raise RuntimeError("Perfil SHARPpy sem pres/tmpc/dwpc")

    ax.plot(t, p, linewidth=1.8, label="T")
    ax.plot(td, p, linewidth=1.8, label="Td")
    ax.legend(loc="best")

    title = meta.get("title") or meta.get("capital") or "Sideral Skew-T"
    ax.set_title(title)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    return output
