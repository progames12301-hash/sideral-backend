from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    id: str
    name: str
    resolution: str
    source: str
    planned: bool = False


@dataclass(frozen=True, slots=True)
class ProductDefinition:
    id: str
    name: str
    unit: str
    palette: str
    category: str
    variable_kind: str = "other"


MODELS = (
    ModelDefinition("ecmwf", "ECMWF IFS", "0.25°", "ECMWF"),
    ModelDefinition("gfs", "GFS", "0.25°", "NOAA/NCEP"),
    ModelDefinition("icon", "ICON", "0.25°", "DWD"),
    ModelDefinition("aifs", "ECMWF AIFS", "0.25°", "ECMWF"),
    ModelDefinition("wrf3", "WRF Sideral 3 km", "3 km", "Sideral", planned=True),
    ModelDefinition("wrf9", "WRF Sideral 9 km", "9 km", "Sideral", planned=True),
)

PRODUCTS = (
    ProductDefinition("qpf1", "Chuva acumulada em 1 hora", "mm", "precipitation", "precipitation", "precipitation"),
    ProductDefinition("qpf3", "Chuva acumulada em 3 horas", "mm", "precipitation", "precipitation", "precipitation"),
    ProductDefinition("qpf6", "Chuva acumulada em 6 horas", "mm", "precipitation", "precipitation", "precipitation"),
    ProductDefinition("qpf12", "Chuva acumulada em 12 horas", "mm", "precipitation", "precipitation", "precipitation"),
    ProductDefinition("qpf24", "Chuva acumulada em 24 horas", "mm", "precipitation", "precipitation", "precipitation"),
    ProductDefinition("qpf48", "Chuva acumulada em 48 horas", "mm", "precipitation", "precipitation", "precipitation"),
    ProductDefinition("qpf72", "Chuva acumulada em 72 horas", "mm", "precipitation", "precipitation", "precipitation"),
    ProductDefinition("qpf_total", "Precipitação total da rodada", "mm", "precipitation", "precipitation", "precipitation"),
    ProductDefinition("sbcape", "SBCAPE", "J/kg", "cape", "convective", "cape"),
    ProductDefinition("mlcape", "MLCAPE", "J/kg", "cape", "convective", "cape"),
    ProductDefinition("mucape", "MUCAPE", "J/kg", "cape", "convective", "cape"),
    ProductDefinition("cin", "Convective Inhibition", "J/kg", "cin", "convective", "cin"),
    ProductDefinition("lifted_index", "Lifted Index", "°C", "temperature", "convective", "temperature"),
    ProductDefinition("lcl", "Altura do LCL", "m", "composite", "convective"),
    ProductDefinition("lfc", "Altura do LFC", "m", "composite", "convective"),
    ProductDefinition("el", "Equilibrium Level", "m", "composite", "convective"),
    ProductDefinition("pwat", "Água precipitável", "mm", "pwat", "convective", "precipitation"),
    ProductDefinition("dcape", "DCAPE", "J/kg", "cape", "convective", "cape"),
    ProductDefinition("shear01", "Bulk Shear 0–1 km", "m/s", "shear", "severe", "wind"),
    ProductDefinition("shear03", "Bulk Shear 0–3 km", "m/s", "shear", "severe", "wind"),
    ProductDefinition("shear06", "Bulk Shear 0–6 km", "m/s", "shear", "severe", "wind"),
    ProductDefinition("srh01", "SRH 0–1 km", "m²/s²", "srh", "severe"),
    ProductDefinition("srh03", "SRH 0–3 km", "m²/s²", "srh", "severe"),
    ProductDefinition("scp", "Supercell Composite Parameter", "", "composite", "severe"),
    ProductDefinition("stp", "Significant Tornado Parameter", "", "composite", "severe"),
    ProductDefinition("ship", "Significant Hail Parameter", "", "composite", "severe"),
    ProductDefinition("updraft_helicity", "Updraft Helicity", "m²/s²", "srh", "severe"),
    ProductDefinition("temp2m", "Temperatura a 2 m", "°C", "temperature", "surface", "temperature"),
    ProductDefinition("temp_min", "Temperatura mínima", "°C", "temperature", "surface", "temperature"),
    ProductDefinition("temp_max", "Temperatura máxima", "°C", "temperature", "surface", "temperature"),
    ProductDefinition("dewpoint2m", "Ponto de orvalho a 2 m", "°C", "temperature", "surface", "temperature"),
    ProductDefinition("humidity2m", "Umidade relativa a 2 m", "%", "humidity", "surface", "humidity"),
    ProductDefinition("mslp", "Pressão ao nível médio do mar", "hPa", "pressure", "surface", "pressure"),
    ProductDefinition("wind10m", "Vento a 10 m", "m/s", "wind", "surface", "wind"),
    ProductDefinition("gust10m", "Rajada a 10 m", "m/s", "wind", "surface", "wind"),
    ProductDefinition("t850_wind", "Temperatura + vento em 850 hPa", "°C / m/s", "temperature", "levels"),
    ProductDefinition("level700", "Umidade + omega + vento em 700 hPa", "% / Pa/s / m/s", "humidity", "levels"),
    ProductDefinition("z500_vort", "Geopotencial + vorticidade em 500 hPa", "dam / 10⁻⁵ s⁻¹", "vorticity", "levels"),
    ProductDefinition("wind250", "Vento em 250 hPa", "m/s", "wind", "levels", "wind"),
    ProductDefinition("thickness", "Espessura 1000–500 hPa", "dam", "pressure", "synoptic"),
    ProductDefinition("mslp_precip", "MSLP + precipitação", "hPa / mm", "pressure", "synoptic"),
    ProductDefinition("surface", "Superfície — MSLP + precipitação + vento", "hPa / mm / m/s", "pressure", "synoptic"),
    ProductDefinition("level850", "850 hPa — temperatura + vento + umidade", "°C / m/s / %", "temperature", "synoptic"),
    ProductDefinition("level500", "500 hPa — geopotencial + vorticidade + vento", "dam / 10⁻⁵ s⁻¹ / m/s", "vorticity", "synoptic"),
    ProductDefinition("level250", "250 hPa — vento + geopotencial", "m/s / dam", "wind", "synoptic"),
)

REGIONS = {
    "south_america": {"label": "América do Sul", "west": -85.0, "east": -30.0, "south": -60.0, "north": 15.0},
    "brazil": {"label": "Brasil", "west": -74.0, "east": -34.0, "south": -34.0, "north": 6.0},
    "south_brazil": {"label": "Sul", "west": -58.5, "east": -47.0, "south": -34.5, "north": -21.5},
    "southeast_brazil": {"label": "Sudeste", "west": -53.5, "east": -39.0, "south": -26.0, "north": -14.0},
    "central_west_brazil": {"label": "Centro-Oeste", "west": -62.0, "east": -45.0, "south": -25.0, "north": -7.0},
    "south_southeast_brazil": {"label": "Sul + Sudeste", "west": -59.0, "east": -38.0, "south": -35.0, "north": -13.0},
}

MODEL_BY_ID = {item.id: item for item in MODELS}
PRODUCT_BY_ID = {item.id: item for item in PRODUCTS}

MULTIMODEL_STATS = {"mean", "median", "min", "max", "spread", "stddev", "p10", "p25", "p50", "p75", "p90"}
