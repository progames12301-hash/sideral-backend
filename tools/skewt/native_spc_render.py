from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def render_native_spc(prof, tmp_dir, meta):
    """Headless Skew-T renderer for CI/SHARPpy.

    Do not instantiate SHARPpy's Qt ``plotSkewT`` widget here. Recent Qt
    bindings reject QActionGroup(exclusive=...), which is the crash seen on
    GitHub Actions. The profile itself remains a real SHARPpy profile.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    out_dir = Path(tmp_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "skewt.png"

    p = np.asarray(getattr(prof, "pres", []), dtype=float)
    t = np.asarray(getattr(prof, "tmpc", []), dtype=float)
    td = np.asarray(getattr(prof, "dwpc", []), dtype=float)
    if p.size == 0 or t.size != p.size or td.size != p.size:
        raise RuntimeError("Perfil SHARPpy inválido: pres/tmpc/dwpc incompatíveis")

    valid = np.isfinite(p) & np.isfinite(t) & np.isfinite(td) & (p > 0)
    p, t, td = p[valid], t[valid], td[valid]
    order = np.argsort(p)[::-1]
    p, t, td = p[order], t[order], td[order]
    if p.size < 3:
        raise RuntimeError("Perfil SHARPpy insuficiente para Skew-T")

    fig, ax = plt.subplots(figsize=(10, 10), dpi=120)

    # SPC-style skewed temperature coordinate: x = T + k ln(p0/p).
    p0 = 1000.0
    skew = 35.0
    y = np.log(p / p0)
    x_t = t + skew * (-y)
    x_td = td + skew * (-y)

    ax.plot(x_t, p, linewidth=1.8, label="Temperatura")
    ax.plot(x_td, p, linewidth=1.8, label="Ponto de orvalho")

    # Isotherms, pressure levels and dry-advection-style reference lines.
    for temp in range(-80, 51, 10):
        ax.plot(temp + skew * (-y), p, linewidth=0.45, alpha=0.28)
    for pressure in (1000, 925, 850, 700, 500, 400, 300, 250, 200, 150, 100):
        ax.axhline(pressure, linewidth=0.5, alpha=0.30)

    ax.set_yscale("log")
    ax.set_ylim(1050, 100)
    ax.set_xlim(-100, 80)
    ax.set_ylabel("Pressão (hPa)")
    ax.set_xlabel("Temperatura (°C) — coordenada Skew-T")
    ax.grid(False)

    title = meta.get("title") or meta.get("capital") or "Sideral Skew-T"
    ax.set_title(title)
    ax.legend(loc="best")
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    return output
