from __future__ import annotations


PALETTES = {
    "precipitation": {"levels": [0.2, 1, 2.5, 5, 7.5, 12.5, 17.5, 22.5, 30, 40, 50, 75, 100, 150, 200, 250, 300], "colors": ["#bdbdbd", "#a8a8a8", "#8f8f8f", "#9cf29c", "#58e468", "#16c83f", "#068b2c", "#176ac6", "#318de5", "#6fc4f0", "#f8e96b", "#ffb63f", "#ff6a20", "#e51d13", "#8f0d0d", "#6f2596"]},
    "cape": {"levels": [0, 250, 500, 1000, 1500, 2000, 3000, 4000, 6000], "colors": ["#f5f5f5", "#c8efb3", "#79d978", "#f3e45a", "#f5ad3c", "#ed5a34", "#b32052", "#7b2d89"]},
    "cin": {"levels": [-500, -250, -150, -100, -50, -25, -10, 0], "colors": ["#2c1e66", "#5140a1", "#347ab7", "#43b3b0", "#99d8a5", "#e7efb0", "#f5f5f5"]},
    "srh": {"levels": [0, 50, 100, 150, 200, 300, 450, 650], "colors": ["#eef4f7", "#8dd3c7", "#5ab4ac", "#dfc27d", "#e08214", "#d73027", "#762a83"]},
    "shear": {"levels": [0, 5, 10, 15, 20, 25, 30, 40], "colors": ["#f2f5f7", "#b8d8e8", "#73a9cf", "#2b83ba", "#abdda4", "#fdae61", "#d7191c"]},
    "temperature": {"levels": [-40, -30, -20, -10, 0, 10, 20, 30, 40, 50], "colors": ["#56268f", "#324fb0", "#4aa9d8", "#b9e3f4", "#93d17a", "#f4df61", "#f3923e", "#b41e2b", "#6d0f1e"]},
    "wind": {"levels": [0, 5, 10, 20, 30, 40, 50, 70, 90], "colors": ["#f3f5f6", "#b7e3da", "#59c7ba", "#3e8fc4", "#7664b6", "#bd3e91", "#e74c3c", "#f5cf4a"]},
    "pressure": {"levels": [960, 980, 990, 1000, 1010, 1020, 1030, 1040, 1050], "colors": ["#56358c", "#3c78b4", "#8bc7c8", "#dce8ca", "#eeeecc", "#e8b85b", "#c8663d", "#8f2d32"]},
    "humidity": {"levels": [0, 20, 40, 60, 70, 80, 90, 100], "colors": ["#9a5b36", "#cf985d", "#e8d49a", "#b7d88a", "#78bd7b", "#52a66d", "#176a56"]},
    "pwat": {"levels": [0, 10, 20, 30, 40, 50, 60, 80], "colors": ["#f4f1e8", "#cfdfb8", "#85c99c", "#4da9a1", "#3978a9", "#6c4e9f", "#b33b78"]},
    "vorticity": {"levels": [-30, -15, -5, 0, 5, 15, 30, 45], "colors": ["#26547c", "#70a9c9", "#d9e5e8", "#f2f2e8", "#f3b562", "#db504a", "#7d1538"]},
    "composite": {"levels": [0, 1, 2, 3, 4, 5, 8], "colors": ["#1c2631", "#355f7c", "#52a58d", "#d2c55b", "#d77b39", "#a52b3d"]},
}


def palette(name: str, probability: bool = False) -> dict[str, list]:
    if probability:
        return {"levels": [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100], "colors": ["#f7fbff", "#d9edf7", "#b7ddec", "#83c9db", "#4badc6", "#2c8caa", "#f0d35c", "#eda142", "#e66a36", "#c9343f"]}
    return PALETTES.get(name, PALETTES["composite"])

