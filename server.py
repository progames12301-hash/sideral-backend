from __future__ import annotations
import datetime as dt
import csv
import io
import html
import json
import math
import os
import re
import time
import zipfile
import struct
import threading
import zlib
import binascii
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlparse, parse_qs
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import requests

# ==============================================================================
# RENDER KEEP-ALIVE
# ==============================================================================
def render_keep_alive() -> None:
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_url:
        print("[KEEP-ALIVE] RENDER_EXTERNAL_URL não definido; keep-alive desativado.")
        return
    while True:
        time.sleep(600)
        try:
            resp = requests.get(f'{render_url.rstrip("/")}/api/health', timeout=10)
            print(f"[KEEP-ALIVE] ping enviado — status HTTP {resp.status_code}")
        except Exception as exc:
            print(f"[KEEP-ALIVE] falhou: {exc}")

# ==============================================================================
# CONFIGURAÇÕES E CAMINHOS
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent
WRF_OUTPUT_DIR = Path(os.environ.get("WRF_OUTPUT_DIR", str(BASE_DIR / "wrf_system" / "output")))
WRF_MODEL_OUTPUTS = {
    "icon": WRF_OUTPUT_DIR / "icon",
    "gfs": WRF_OUTPUT_DIR / "gfs",
    "ecmwf": WRF_OUTPUT_DIR / "ecmwf",
}
GFS_DATA_DIR = BASE_DIR / "wrf_system" / "data" / "gfs"
GFS_WSL_DATA_DIR = Path(r"\wsl.localhost\Ubuntu-22.04\home\bryan\sideral_wrf\data\gfs")
GFS_DATA_DIRS = [GFS_DATA_DIR, GFS_WSL_DATA_DIR]
DEFAULT_HOST = os.environ.get("HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("PORT", "8766"))


# SIDERAL_MODEL_GITHUB_REMOTE_V2
# Os produtos leves publicados pelo GitHub Actions sao lidos pelo Render.
# GFS usa WRF 4 km/REFL_10CM; ICON e ECMWF usam campos oficiais diretos.
MODEL_REMOTE_BASES = {
    "gfs": os.environ.get(
        "WRF_GITHUB_RAW_BASE",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/wrf-data",
    ).rstrip("/"),
    "icon": os.environ.get(
        "ICON_GITHUB_RAW_BASE",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/icon-data",
    ).rstrip("/"),
    "ecmwf": os.environ.get(
        "ECMWF_GITHUB_RAW_BASE",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/ecmwf-data",
    ).rstrip("/"),
}
MODEL_REMOTE_CACHE_SECONDS = int(os.environ.get("MODEL_REMOTE_CACHE_SECONDS", "120"))
model_remote_cache: dict[str, dict[str, Any]] = {
    key: {"metadata": None, "frames": {}} for key in MODEL_REMOTE_BASES
}
model_remote_lock = threading.Lock()

INMET_STATIONS_URL = "https://apitempo.inmet.gov.br/estacoes/T"
INMET_OBSERVATION_URL = "https://apitempo.inmet.gov.br/estacao/{start}/{end}/{station}"
INMET_HISTORICAL_ZIP_URL = "https://portal.inmet.gov.br/uploads/dadoshistoricos/{year}.zip"
INMET_CACHE_DIR = BASE_DIR / "inmet_cache"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SYNOPTIC_CACHE_DIR = BASE_DIR / "synoptic_cache"
SYNOPTIC_SVG_PATH = SYNOPTIC_CACHE_DIR / "sideral_synoptic.svg"
SYNOPTIC_META_PATH = SYNOPTIC_CACHE_DIR / "sideral_synoptic.json"
SYNOPTIC_PNG_PATH = SYNOPTIC_CACHE_DIR / "official_synoptic.png"
INMET_SYNOPTIC_ARCHIVE_URL = "https://portal.inmet.gov.br/uploads/cartasinotica/{year}.zip"
INMET_SYNOPTIC_PAGE = "https://portal.inmet.gov.br/cartasinotica"
SYNOPTIC_CACHE_SECONDS = 30 * 60
synoptic_cache_lock = threading.Lock()

INMET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}
INMET_STATION_CODE_RE = re.compile(r"^[A-Z0-9_-]{2,12}$")

IPMET_RADAR_PAGE = "https://www.ipmetradar.com.br/mobile2/openlayers/ipmet/radar.php"
IPMET_WMS_URL = "https://www.ipmetradar.com.br/cgi-bin/mapserv.fcgi"
IPMET_MAP_FILE = "/home/webadm/alerta/dados/ppi/ultimo.map"
IPMET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SideralMeteorologia/1.0)",
    "Referer": IPMET_RADAR_PAGE,
    "Accept": "image/avif,image/webp,image/apng,image/png,*/*;q=0.8",
}

REDEMET_API_URL = "https://api-redemet.decea.mil.br"
REDEMET_API_KEY = os.environ.get("REDEMET_API_KEY", "kvNQbm99G0YQQMjUrhqKWiZxjmnw0PRf8JxOe26Q")
REDEMET_PRODUCTS = {"03km", "05km", "07km", "10km", "maxcappi"}
REDEMET_ICAO_RE = re.compile(r"^[A-Z]{4}$")
REDEMET_STATION_CACHE_SECONDS = 5 * 60

RAINVIEWER_META_URL = "https://api.rainviewer.com/public/weather-maps.json"
INEA_RADAR_TOOL_URL = "https://radartool.inea.rj.gov.br/radar-tool"
SIMEPAR_RADAR_URL = "https://lb01.simepar.br/riak/pgw-radar"

INMET_CACHE_SECONDS = 60
inmet_observation_cache: dict[str, dict[str, Any]] = {}
inmet_station_catalog_cache: dict[str, Any] = {"saved_at": 0.0, "data": None}
inea_radar_image_cache: dict[str, bytes] = {}
redemet_station_catalog_cache: dict[str, Any] = {"saved_at": 0.0, "data": None}
redemet_station_observation_cache: dict[str, dict[str, Any]] = {}

# Sondagens ECMWF + SHARPpy. O cache evita repetir download/processamento no Render.
SOUNDING_CACHE_SECONDS = 15 * 60
sounding_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
ECMWF_PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100]
ECMWF_GRIB_CACHE_SECONDS = 3 * 60 * 60
ECMWF_SOUNDING_CACHE_DIR = Path(
    os.environ.get(
        "ECMWF_CACHE_DIR",
        "/tmp/sideral_ecmwf" if os.environ.get("RENDER", "").lower() == "true" else str(BASE_DIR / "ecmwf_sounding_cache"),
    )
)
ecmwf_download_lock = threading.Lock()


def png_black_to_transparent(body: bytes, threshold: int = 12) -> bytes:
    if not body.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Arquivo não é PNG")
    position, ihdr, idat = 8, None, []
    while position + 12 <= len(body):
        length = struct.unpack(">I", body[position:position + 4])[0]
        kind = body[position + 4:position + 8]
        data = body[position + 8:position + 8 + length]
        position += 12 + length
        if kind == b"IHDR": ihdr = data
        elif kind == b"IDAT": idat.append(data)
        elif kind == b"IEND": break
    if not ihdr or len(ihdr) != 13:
        raise ValueError("PNG sem IHDR")
    width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", ihdr)
    if bit_depth != 8 or color_type != 6 or interlace != 0:
        return body
    stride, bpp = width * 4, 4
    packed = zlib.decompress(b"".join(idat))
    if len(packed) != height * (stride + 1):
        raise ValueError("Tamanho PNG inesperado")
    rows, offset, previous = [], 0, bytearray(stride)
    for _ in range(height):
        filter_type = packed[offset]; offset += 1
        source = packed[offset:offset + stride]; offset += stride
        row = bytearray(stride)
        for index, value in enumerate(source):
            left = row[index - bpp] if index >= bpp else 0
            up = previous[index]
            upper_left = previous[index - bpp] if index >= bpp else 0
            if filter_type == 0: predictor = 0
            elif filter_type == 1: predictor = left
            elif filter_type == 2: predictor = up
            elif filter_type == 3: predictor = (left + up) // 2
            elif filter_type == 4:
                estimate = left + up - upper_left
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - upper_left))
                predictor = left if distances[0] <= distances[1] and distances[0] <= distances[2] else up if distances[1] <= distances[2] else upper_left
            else: raise ValueError("Filtro PNG inválido")
            row[index] = (value + predictor) & 255
        for index in range(0, stride, 4):
            if row[index] <= threshold and row[index + 1] <= threshold and row[index + 2] <= threshold:
                row[index + 3] = 0
        rows.append(b"\x00" + bytes(row)); previous = row

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b"")


class ExternalAPIError(RuntimeError): pass
class WRFDomainError(RuntimeError): pass

def inmet_safe_float(value: Any) -> float | None:
    if value is None: return None
    text = str(value).strip().replace(",", ".")
    if text.lower() in {"", "null", "none", "nan", "-9999", "-9999.0", "9999", "9999.0"}: return None
    try: number = float(text)
    except (TypeError, ValueError): return None
    if abs(number) >= 9990: return None
    return number

def request_headers_with_optional_inmet_token() -> dict[str, str]:
    headers = dict(INMET_HEADERS)
    token = os.environ.get("INMET_TOKEN") or os.environ.get("AGROBR_INMET_TOKEN")
    if token: headers["Authorization"] = f"Bearer {token}"
    return headers

def get_inmet_station_catalog() -> list[dict[str, Any]]:
    now_monotonic = time.monotonic()
    cached_data = inmet_station_catalog_cache.get("data")
    if cached_data is not None and now_monotonic - float(inmet_station_catalog_cache.get("saved_at", 0.0)) < 3600:
        return cached_data
    data = fetch_inmet_json(INMET_STATIONS_URL, timeout=25)
    if not isinstance(data, list): raise RuntimeError("Catálogo INMET em formato inesperado.")
    inmet_station_catalog_cache["saved_at"] = now_monotonic
    inmet_station_catalog_cache["data"] = data
    return data

def find_inmet_station(station_code: str) -> dict[str, Any] | None:
    for station in get_inmet_station_catalog():
        if isinstance(station, dict) and str(station.get("CD_ESTACAO", "")).upper() == station_code:
            return station
    return None

def inmet_normalize_hour(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return (digits or "0000").zfill(4)[-4:]

def inmet_record_datetime_utc(record: dict[str, Any]) -> dt.datetime | None:
    date_text = str(record.get("DT_MEDICAO") or "").strip()
    if not date_text: return None
    hour_text = inmet_normalize_hour(record.get("HR_MEDICAO"))
    try: return dt.datetime.strptime(f"{date_text} {hour_text}", "%Y-%m-%d %H%M").replace(tzinfo=dt.timezone.utc)
    except ValueError: return None

def inmet_has_weather_value(record: dict[str, Any]) -> bool:
    return any(inmet_safe_float(record.get(key)) is not None for key in ("TEM_INS", "TEM_SEN", "UMD_INS", "CHUVA", "VEN_VEL", "VEN_RAJ", "PRE_INS", "PTO_INS", "VEN_DIR", "RAD_GLO"))

def inmet_normalize_observation(record: dict[str, Any]) -> dict[str, Any]:
    measured_utc = inmet_record_datetime_utc(record)
    measured_local = measured_utc.astimezone(dt.timezone(dt.timedelta(hours=-3))) if measured_utc else None
    return {
        "codigo": record.get("CD_ESTACAO"),
        "observado_em_utc": measured_utc.isoformat() if measured_utc else None,
        "data_hora_utc": measured_utc.strftime("%d/%m/%Y %H:%M UTC") if measured_utc else None,
        "data_hora_brasilia": measured_local.strftime("%d/%m/%Y %H:%M") if measured_local else None,
        "temperatura_c": inmet_safe_float(record.get("TEM_INS")),
        "sensacao_c": inmet_safe_float(record.get("TEM_SEN")),
        "umidade_pct": inmet_safe_float(record.get("UMD_INS")),
        "chuva_mm": inmet_safe_float(record.get("CHUVA")),
        "vento_ms": inmet_safe_float(record.get("VEN_VEL")),
        "rajada_ms": inmet_safe_float(record.get("VEN_RAJ")),
        "direcao_vento_graus": inmet_safe_float(record.get("VEN_DIR")),
        "pressao_hpa": inmet_safe_float(record.get("PRE_INS")),
        "orvalho_c": inmet_safe_float(record.get("PTO_INS")),
        "radiacao_kjm2": inmet_safe_float(record.get("RAD_GLO")),
    }

def fetch_inmet_json(url: str, timeout: int = 35) -> Any:
    response = requests.get(url, headers=request_headers_with_optional_inmet_token(), timeout=timeout)
    if response.status_code == 204 or not response.content: return []
    response.raise_for_status()
    return response.json()

def inmet_historical_zip_path(year: int) -> Path:
    INMET_CACHE_DIR.mkdir(exist_ok=True)
    path = INMET_CACHE_DIR / f"{year}.zip"
    if path.exists() and path.stat().st_size > 0: return path
    url = INMET_HISTORICAL_ZIP_URL.format(year=year)
    with requests.get(url, headers=INMET_HEADERS, timeout=180, stream=True) as response:
        response.raise_for_status()
        temp_path = path.with_suffix(".zip.tmp")
        with temp_path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk: file.write(chunk)
        temp_path.replace(path)
    return path

def inmet_parse_historical_datetime(date_text: str, hour_text: str) -> dt.datetime | None:
    hour_digits = inmet_normalize_hour(hour_text)
    clean_date = date_text.strip().replace("-", "/")
    for fmt in ("%Y/%m/%d", "%d/%m/%Y"):
        try:
            date_value = dt.datetime.strptime(clean_date, fmt)
            return date_value.replace(hour=int(hour_digits[:2]), minute=int(hour_digits[2:4]), tzinfo=dt.timezone.utc)
        except ValueError: continue
    return None

def normalize_historical_observation(row: dict[str, str], station_code: str) -> dict[str, Any]:
    measured_utc = inmet_parse_historical_datetime(row.get("Data", ""), row.get("Hora UTC", ""))
    measured_local = measured_utc.astimezone(dt.timezone(dt.timedelta(hours=-3))) if measured_utc else None
    return {
        "codigo": station_code,
        "observado_em_utc": measured_utc.isoformat() if measured_utc else None,
        "data_hora_utc": measured_utc.strftime("%d/%m/%Y %H:%M UTC") if measured_utc else None,
        "data_hora_brasilia": measured_local.strftime("%d/%m/%Y %H:%M") if measured_local else None,
        "temperatura_c": inmet_safe_float(row.get("TEMPERATURA DO AR - BULBO SECO, HORARIA (°C)")),
        "sensacao_c": None,
        "umidade_pct": inmet_safe_float(row.get("UMIDADE RELATIVA DO AR, HORARIA (%)")),
        "chuva_mm": inmet_safe_float(row.get("PRECIPITAÇÃO TOTAL, HORÁRIO (mm)")),
        "vento_ms": inmet_safe_float(row.get("VENTO, VELOCIDADE HORARIA (m/s)")),
        "rajada_ms": inmet_safe_float(row.get("VENTO, RAJADA MAXIMA (m/s)")),
        "direcao_vento_graus": inmet_safe_float(row.get("VENTO, DIREÇÃO HORARIA (gr) (° (gr))")),
        "pressao_hpa": inmet_safe_float(row.get("PRESSAO ATMOSFERICA AO NIVEL DA ESTACAO, HORARIA (mB)")),
        "orvalho_c": inmet_safe_float(row.get("TEMPERATURA DO PONTO DE ORVALHO (°C)")),
        "radiacao_kjm2": inmet_safe_float(row.get("RADIACAO GLOBAL (Kj/m²)")),
    }

def historical_row_has_weather_value(row: dict[str, str]) -> bool:
    keys = ("TEMPERATURA DO AR - BULBO SECO, HORARIA (°C)", "UMIDADE RELATIVA DO AR, HORARIA (%)", "PRECIPITAÇÃO TOTAL, HORÁRIO (mm)", "VENTO, VELOCIDADE HORARIA (m/s)", "VENTO, RAJADA MAXIMA (m/s)", "PRESSAO ATMOSFERICA AO NIVEL DA ESTACAO, HORARIA (mB)", "TEMPERATURA DO PONTO DE ORVALHO (°C)", "VENTO, DIREÇÃO HORARIA (gr) (° (gr))", "RADIACAO GLOBAL (Kj/m²)")
    return any(inmet_safe_float(row.get(key)) is not None for key in keys)

def get_latest_inmet_historical_observation(station_code: str, year: int) -> dict[str, Any]:
    zip_path = inmet_historical_zip_path(year)
    with zipfile.ZipFile(zip_path) as archive:
        station_files = [name for name in archive.namelist() if f" {station_code} " in name.upper() and name.upper().endswith(".CSV")]
        if not station_files:
            return {"estacao": station_code, "observacao": None, "registros_recebidos": 0, "fonte": f"INMET dados históricos {year}"}
        with archive.open(station_files[0]) as raw_file:
            text = raw_file.read().decode("latin1")
        lines = text.splitlines()
        header_index = next((index for index, line in enumerate(lines) if line.startswith("Data;Hora UTC;")), None)
        if header_index is None: raise RuntimeError("CSV histórico do INMET sem cabeçalho esperado.")
        csv_text = "\n".join(lines[header_index:])
        rows = list(csv.DictReader(io.StringIO(csv_text), delimiter=";"))
        for row in reversed(rows):
            measured_utc = inmet_parse_historical_datetime(row.get("Data", ""), row.get("Hora UTC", ""))
            if measured_utc and historical_row_has_weather_value(row):
                return {"estacao": station_code, "observacao": normalize_historical_observation(row, station_code), "registros_recebidos": len(rows), "fonte": f"INMET dados históricos {year}", "idade_segundos": max(0, int((dt.datetime.now(dt.timezone.utc) - measured_utc).total_seconds())), "arquivo_consultado": station_files[0]}
        return {"estacao": station_code, "observacao": None, "registros_recebidos": len(rows), "fonte": f"INMET dados históricos {year}", "arquivo_consultado": station_files[0]}

def get_open_meteo_observation(station_code: str) -> dict[str, Any]:
    station = find_inmet_station(station_code)
    if not station: return {"estacao": station_code, "observacao": None, "fonte": "Open-Meteo: estação INMET não encontrada"}
    latitude = inmet_safe_float(station.get("VL_LATITUDE"))
    longitude = inmet_safe_float(station.get("VL_LONGITUDE"))
    if latitude is None or longitude is None: return {"estacao": station_code, "observacao": None, "fonte": "Open-Meteo: coordenadas da estação indisponíveis"}
    response = requests.get(OPEN_METEO_URL, params={"latitude": latitude, "longitude": longitude, "current": ",".join(["temperature_2m", "relative_humidity_2m", "apparent_temperature", "precipitation", "surface_pressure", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "shortwave_radiation"]), "wind_speed_unit": "ms", "timezone": "UTC"}, timeout=20)
    response.raise_for_status()
    data = response.json()
    current = data.get("current") if isinstance(data, dict) else None
    if not isinstance(current, dict): raise RuntimeError("Open-Meteo retornou formato inesperado.")
    measured_text = current.get("time")
    measured_utc = dt.datetime.fromisoformat(str(measured_text)).replace(tzinfo=dt.timezone.utc) if measured_text else None
    measured_local = measured_utc.astimezone(dt.timezone(dt.timedelta(hours=-3))) if measured_utc else None
    return {"estacao": station_code, "observacao": {"codigo": station_code, "observado_em_utc": measured_utc.isoformat() if measured_utc else None, "data_hora_utc": measured_utc.strftime("%d/%m/%Y %H:%M UTC") if measured_utc else None, "data_hora_brasilia": measured_local.strftime("%d/%m/%Y %H:%M") if measured_local else None, "temperatura_c": inmet_safe_float(current.get("temperature_2m")), "sensacao_c": inmet_safe_float(current.get("apparent_temperature")), "umidade_pct": inmet_safe_float(current.get("relative_humidity_2m")), "chuva_mm": inmet_safe_float(current.get("precipitation")), "vento_ms": inmet_safe_float(current.get("wind_speed_10m")), "rajada_ms": inmet_safe_float(current.get("wind_gusts_10m")), "direcao_vento_graus": inmet_safe_float(current.get("wind_direction_10m")), "pressao_hpa": inmet_safe_float(current.get("surface_pressure")), "orvalho_c": None, "radiacao_kjm2": None}, "fonte": "Open-Meteo quase em tempo real no ponto da estação INMET", "idade_segundos": max(0, int((dt.datetime.now(dt.timezone.utc) - measured_utc).total_seconds())) if measured_utc else None, "coordenadas_consultadas": {"latitude": latitude, "longitude": longitude}}

def fetch_open_meteo_synoptic_points() -> tuple[list[dict[str, Any]], str | None]:
    lats = [lat for lat in range(-55, 16, 5)]
    lons = [lon for lon in range(-85, -29, 5)]
    pairs = [(lat, lon) for lat in lats for lon in lons]
    points: list[dict[str, Any]] = []
    valid_time = None
    for start in range(0, len(pairs), 80):
        chunk = pairs[start:start + 80]
        response = requests.get(OPEN_METEO_URL, params={"latitude": ",".join(str(lat) for lat, _ in chunk), "longitude": ",".join(str(lon) for _, lon in chunk), "current": "pressure_msl,wind_speed_10m,wind_direction_10m,precipitation", "wind_speed_unit": "ms", "timezone": "UTC"}, timeout=35)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict): payload = [payload]
        if not isinstance(payload, list): raise RuntimeError("Open-Meteo retornou formato inesperado para a carta sinótica.")
        for item in payload:
            if not isinstance(item, dict): continue
            current = item.get("current")
            if not isinstance(current, dict): continue
            if valid_time is None: valid_time = current.get("time")
            pressure = inmet_safe_float(current.get("pressure_msl"))
            wind_speed = inmet_safe_float(current.get("wind_speed_10m"))
            wind_dir = inmet_safe_float(current.get("wind_direction_10m"))
            precipitation = inmet_safe_float(current.get("precipitation"))
            if pressure is None: continue
            points.append({"lat": float(item.get("latitude")), "lon": float(item.get("longitude")), "pressure": pressure, "wind_speed": wind_speed or 0.0, "wind_dir": wind_dir or 0.0, "precipitation": precipitation or 0.0})
    return points, valid_time

def project_synoptic(lon: float, lat: float, width: int = 1200, height: int = 860) -> tuple[float, float]:
    west, east = -85.0, -30.0
    south, north = -55.0, 15.0
    left, top = 70.0, 86.0
    plot_width, plot_height = width - 140.0, height - 160.0
    x = left + (lon - west) / (east - west) * plot_width
    y = top + (north - lat) / (north - south) * plot_height
    return x, y

def precipitation_color(value: float) -> str:
    if value >= 10: return "#7c2d12"
    if value >= 5: return "#dc2626"
    if value >= 2: return "#f97316"
    if value >= 1: return "#facc15"
    if value >= 0.2: return "#22c55e"
    if value > 0: return "#93c5fd"
    return "#ffffff"

def geojson_paths_svg() -> str:
    geojson_path = BASE_DIR / "brazil-states.geojson"
    if not geojson_path.exists(): return ""
    try: data = json.loads(geojson_path.read_text(encoding="utf-8"))
    except Exception: return ""
    paths = []
    def ring_path(ring: list[list[float]]) -> str:
        parts = []
        for index, coord in enumerate(ring):
            if len(coord) < 2: continue
            x, y = project_synoptic(float(coord[0]), float(coord[1]))
            parts.append(("M" if index == 0 else "L") + f"{x:.1f},{y:.1f}")
        return " ".join(parts)
    for feature in data.get("features", []):
        geometry = feature.get("geometry", {})
        coordinates = geometry.get("coordinates", [])
        geometry_type = geometry.get("type")
        polygons = coordinates if geometry_type == "MultiPolygon" else [coordinates]
        for polygon in polygons:
            if not polygon: continue
            path_data = ring_path(polygon[0])
            if path_data: paths.append(f'<path d="{path_data} Z" fill="rgba(255,255,255,.18)" stroke="#334155" stroke-width="0.9"/>')
    return "\n".join(paths)

def build_synoptic_svg(points: list[dict[str, Any]], valid_time: str | None) -> str:
    width, height = 1200, 860
    by_key = {(round(point["lat"] / 5) * 5, round(point["lon"] / 5) * 5): point for point in points}
    lats = sorted({key[0] for key in by_key})
    lons = sorted({key[1] for key in by_key})
    valid_label = valid_time or "horário indisponível"
    svg_parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="1200" height="860" fill="#dbeafe"/>', '<rect x="70" y="86" width="1060" height="700" rx="10" fill="#eff6ff" stroke="#93c5fd"/>']
    for lon in range(-85, -29, 10):
        x1, y1 = project_synoptic(lon, -55)
        x2, y2 = project_synoptic(lon, 15)
        svg_parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#bfdbfe" stroke-width="1"/>')
        svg_parts.append(f'<text x="{x1:.1f}" y="810" fill="#64748b" font-size="14" text-anchor="middle">{abs(lon)}W</text>')
    for lat in range(-50, 16, 10):
        x1, y1 = project_synoptic(-85, lat)
        x2, y2 = project_synoptic(-30, lat)
        svg_parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#bfdbfe" stroke-width="1"/>')
        label = f'{abs(lat)}S' if lat < 0 else f'{lat}N'
        svg_parts.append(f'<text x="42" y="{y1 + 4:.1f}" fill="#64748b" font-size="14" text-anchor="middle">{label}</text>')
    cell_w = 1060 / max(1, len(lons) - 1)
    cell_h = 700 / max(1, len(lats) - 1)
    for point in points:
        if point["precipitation"] <= 0: continue
        x, y = project_synoptic(point["lon"], point["lat"])
        svg_parts.append(f'<rect x="{x - cell_w / 2:.1f}" y="{y - cell_h / 2:.1f}" width="{cell_w:.1f}" height="{cell_h:.1f}" fill="{precipitation_color(point["precipitation"])}" opacity="0.45"/>')
    svg_parts.append(geojson_paths_svg())
    for point in points[::3]:
        x, y = project_synoptic(point["lon"], point["lat"])
        svg_parts.append(f'<text x="{x:.1f}" y="{y - 8:.1f}" text-anchor="middle" fill="#334155" font-size="10">{point["pressure"]:.0f}</text>')
    for point in points[::2]:
        x, y = project_synoptic(point["lon"], point["lat"])
        angle = math.radians(point["wind_dir"])
        speed = min(28.0, 5.0 + point["wind_speed"] * 2.0)
        dx = -math.sin(angle) * speed
        dy = math.cos(angle) * speed
        svg_parts.append(f'<line x1="{x - dx / 2:.1f}" y1="{y - dy / 2:.1f}" x2="{x + dx / 2:.1f}" y2="{y + dy / 2:.1f}" stroke="#0369a1" stroke-width="1.8" opacity="0.72"/>')
        svg_parts.append(f'<circle cx="{x + dx / 2:.1f}" cy="{y + dy / 2:.1f}" r="2.2" fill="#0369a1" opacity="0.72"/>')
    low = min(points, key=lambda item: item["pressure"])
    high = max(points, key=lambda item: item["pressure"])
    for point, label, color in ((low, "B", "#dc2626"), (high, "A", "#2563eb")):
        x, y = project_synoptic(point["lon"], point["lat"])
        svg_parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="18" fill="#ffffff" stroke="{color}" stroke-width="3"/>')
        svg_parts.append(f'<text x="{x:.1f}" y="{y + 7:.1f}" text-anchor="middle" fill="{color}" font-size="22" font-weight="900">{label}</text>')
        svg_parts.append(f'<text x="{x:.1f}" y="{y + 36:.1f}" text-anchor="middle" fill="#0f172a" font-size="13" font-weight="800">{point["pressure"]:.0f} hPa</text>')
    svg_parts.extend(['<rect x="0" y="0" width="1200" height="66" fill="#0f172a"/>', '<text x="42" y="42" fill="#ffffff" font-size="28" font-weight="900">Carta Sinótica Sideral</text>', f'<text x="1160" y="38" fill="#cbd5e1" font-size="16" text-anchor="end">Válida: {html.escape(valid_label)} UTC</text>', '<rect x="76" y="710" width="310" height="64" rx="8" fill="rgba(255,255,255,.86)" stroke="#cbd5e1"/>', '<text x="94" y="735" fill="#0f172a" font-size="14" font-weight="900">Camadas</text>', '<text x="94" y="758" fill="#334155" font-size="13">Isóbaras: pressão ao nível do mar • Setas: vento 10 m</text>', '<text x="76" y="836" fill="#475569" font-size="13">Carta sinótica Sideral Meteorologia</text>', '</svg>'])
    return "\n".join(svg_parts)

def ensure_synoptic_chart(force: bool = False) -> dict[str, Any]:
    SYNOPTIC_CACHE_DIR.mkdir(exist_ok=True)
    with synoptic_cache_lock:
        now = time.time()
        if not force and SYNOPTIC_PNG_PATH.exists() and SYNOPTIC_META_PATH.exists() and now - SYNOPTIC_PNG_PATH.stat().st_mtime < SYNOPTIC_CACHE_SECONDS:
            return json.loads(SYNOPTIC_META_PATH.read_text(encoding="utf-8"))
        last_error: Exception | None = None
        current_year = dt.datetime.now(dt.timezone.utc).year
        for year in (current_year, current_year - 1):
            try:
                image, valid_time, filename, archive_url = fetch_latest_inmet_synoptic(year)
                SYNOPTIC_PNG_PATH.write_bytes(image)
                valid_datetime = dt.datetime.fromisoformat(valid_time)
                delayed = dt.datetime.now(dt.timezone.utc) - valid_datetime > dt.timedelta(hours=36)
                metadata = {"image": "/api/sinotica/chart.png", "valid_time_utc": valid_time, "updated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "source": "Instituto Nacional de Meteorologia (INMET)", "official_page": INMET_SYNOPTIC_PAGE, "archive": archive_url, "filename": filename, "cache_seconds": SYNOPTIC_CACHE_SECONDS, "stale": False, "delayed": delayed}
                SYNOPTIC_META_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                return metadata
            except Exception as exc: last_error = exc
        if SYNOPTIC_PNG_PATH.exists() and SYNOPTIC_META_PATH.exists():
            metadata = json.loads(SYNOPTIC_META_PATH.read_text(encoding="utf-8"))
            metadata["stale"] = True
            metadata["warning"] = "A fonte oficial não respondeu; exibindo a última carta salva."
            return metadata
        raise RuntimeError(f"Não foi possível obter a carta oficial do INMET: {last_error}")

def remote_zip_range(url: str, start: int, end: int, total_size: int) -> bytes:
    response = requests.get(url, headers={**INMET_HEADERS, "Range": f"bytes={start}-{end}"}, timeout=35)
    response.raise_for_status()
    body = response.content
    expected = end - start + 1
    if response.status_code == 206 and len(body) == expected: return body
    if response.status_code == 200 and len(body) == total_size: return body[start:end + 1]
    raise RuntimeError("O servidor do INMET não respeitou a leitura parcial do arquivo.")

def fetch_latest_inmet_synoptic(year: int) -> tuple[bytes, str, str, str]:
    archive_url = INMET_SYNOPTIC_ARCHIVE_URL.format(year=year)
    head = requests.head(archive_url, headers=INMET_HEADERS, timeout=25, allow_redirects=True)
    head.raise_for_status()
    total_size = int(head.headers.get("Content-Length", "0"))
    if total_size < 100: raise RuntimeError("Arquivo anual de cartas sinóticas vazio.")
    tail_start = max(0, total_size - 131072)
    tail = remote_zip_range(archive_url, tail_start, total_size - 1, total_size)
    eocd_position = tail.rfind(b"PK\x05\x06")
    if eocd_position < 0 or eocd_position + 22 > len(tail): raise RuntimeError("Diretório do arquivo de cartas sinóticas não encontrado.")
    _, _, _, _, _, directory_size, directory_offset, _ = struct.unpack("<4s4H2LH", tail[eocd_position:eocd_position + 22])
    directory_end = directory_offset + directory_size - 1
    if directory_offset >= tail_start and directory_end < total_size:
        relative = directory_offset - tail_start
        directory = tail[relative:relative + directory_size]
    else:
        directory = remote_zip_range(archive_url, directory_offset, directory_end, total_size)
    candidates = []
    position = 0
    while position + 46 <= len(directory) and directory[position:position + 4] == b"PK\x01\x02":
        fields = struct.unpack("<4s6H3L5H2L", directory[position:position + 46])
        flags, compression = fields[3], fields[4]
        crc32_value, compressed_size, uncompressed_size = fields[7], fields[8], fields[9]
        filename_size, extra_size, comment_size = fields[10], fields[11], fields[12]
        local_offset = fields[16]
        name_bytes = directory[position + 46:position + 46 + filename_size]
        filename = name_bytes.decode("utf-8" if flags & 0x800 else "cp437", errors="replace")
        match = re.fullmatch(r"web_AS_analise_(\d{12})_\+0\.png", filename)
        if match: candidates.append((match.group(1), filename, local_offset, compressed_size, uncompressed_size, compression, crc32_value))
        position += 46 + filename_size + extra_size + comment_size
    if not candidates: raise RuntimeError(f"Nenhuma carta sinótica encontrada no arquivo de {year}.")
    stamp, filename, local_offset, compressed_size, uncompressed_size, compression, expected_crc = max(candidates)
    local_header = remote_zip_range(archive_url, local_offset, local_offset + 29, total_size)
    local_fields = struct.unpack("<4s5H3L2H", local_header)
    if local_fields[0] != b"PK\x03\x04": raise RuntimeError("Cabeçalho da imagem sinótica inválido.")
    data_start = local_offset + 30 + local_fields[9] + local_fields[10]
    compressed = remote_zip_range(archive_url, data_start, data_start + compressed_size - 1, total_size)
    if compression == 8: image = zlib.decompress(compressed, -zlib.MAX_WBITS)
    elif compression == 0: image = compressed
    else: raise RuntimeError(f"Compressão ZIP não suportada: {compression}.")
    if len(image) != uncompressed_size or not image.startswith(b"\x89PNG\r\n\x1a\n"): raise RuntimeError("A imagem extraída do INMET está incompleta.")
    if (binascii.crc32(image) & 0xffffffff) != expected_crc: raise RuntimeError("A verificação da imagem sinótica falhou.")
    valid_time = dt.datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc).isoformat()
    return image, valid_time, filename, archive_url

def get_latest_inmet_observation(station_code: str) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    end_date = now.date()
    start_date = end_date - dt.timedelta(days=2)
    url = INMET_OBSERVATION_URL.format(start=start_date.isoformat(), end=end_date.isoformat(), station=station_code)
    data = fetch_inmet_json(url)
    if not isinstance(data, list): raise RuntimeError("O INMET retornou formato inesperado para a estação.")
    candidates: list[tuple[dt.datetime, dict[str, Any]]] = []
    for record in data:
        if not isinstance(record, dict): continue
        measured_utc = inmet_record_datetime_utc(record)
        if measured_utc and inmet_has_weather_value(record): candidates.append((measured_utc, record))
    historical = get_latest_inmet_historical_observation(station_code, now.year)
    live_error = None
    if candidates:
        latest_utc, latest_record = max(candidates, key=lambda item: item[0])
        live = {"estacao": station_code, "observacao": inmet_normalize_observation(latest_record), "registros_recebidos": len(data), "fonte": "INMET tempo real", "idade_segundos": max(0, int((now - latest_utc).total_seconds())), "url_consultada": url}
    else:
        try:
            live = get_open_meteo_observation(station_code)
            live["url_consultada"] = url
            live["api_registros_recebidos"] = len(data)
        except Exception as exc:
            live_error = f"{type(exc).__name__}: {exc}"
            live = {"estacao": station_code, "observacao": None, "fonte": "Ao vivo indisponível", "url_consultada": url, "api_registros_recebidos": len(data)}
    return {"estacao": station_code, "observacao": live.get("observacao"), "fonte": live.get("fonte"), "idade_segundos": live.get("idade_segundos"), "url_consultada": url, "api_registros_recebidos": len(data), "ao_vivo": live, "historico_inmet": historical, "erro_ao_vivo": live_error}

def precipitation_to_dbz(precipitation_mm_per_hour: float) -> float:
    if precipitation_mm_per_hour <= 0: return 0.0
    return max(0.0, min(75.0, 25.0 + 10.0 * math.log10(precipitation_mm_per_hour)))

def wind_direction_deg(u10: float, v10: float) -> float:
    if u10 == 0 and v10 == 0: return 0.0
    return (270.0 - math.degrees(math.atan2(v10, u10))) % 360.0

def hydrometeor_reflectivity_dbz(dataset: Any, t2m: Any) -> Any:
    import numpy as np
    shape = np.asarray(t2m).shape
    z_linear = np.zeros(shape, dtype=float)
    species = {"QRAIN": 4.0e11, "QSNOW": 1.2e11, "QGRAUP": 9.0e11, "QHAIL": 1.4e12}
    for name, scale in species.items():
        if name not in dataset: continue
        mixing_ratio = np.maximum(dataset[name].isel(Time=0).to_numpy(), 0.0)
        column_max = np.nanmax(mixing_ratio, axis=0)
        z_linear += scale * np.power(column_max, 1.25)
    if "QCLOUD" in dataset:
        cloud = np.nanmax(np.maximum(dataset["QCLOUD"].isel(Time=0).to_numpy(), 0.0), axis=0)
        z_linear += np.where(cloud > 2.5e-4, 1.2e8 * np.power(cloud, 1.5), 0.0)
    log_z_linear = np.zeros_like(z_linear)
    positive_mask = z_linear > 0
    log_z_linear[positive_mask] = np.log10(z_linear[positive_mask])
    return np.where(z_linear > 1.0, np.clip(10.0 * log_z_linear, 0.0, 75.0), 0.0)

def approximate_reflectivity_dbz(dataset: Any, t2m: Any, precip_rate: Any) -> Any:
    import numpy as np
    hydrometeor_refl = hydrometeor_reflectivity_dbz(dataset, t2m)
    precip_refl = np.vectorize(precipitation_to_dbz)(precip_rate)
    mask = precip_rate > 0.02
    for name in ("QRAIN", "QSNOW", "QGRAUP", "QHAIL"):
        if name in dataset:
            column_max = np.nanmax(np.maximum(dataset[name].isel(Time=0).to_numpy(), 0.0), axis=0)
            mask |= column_max > 4.0e-6
    return np.where(mask, np.maximum(hydrometeor_refl, precip_refl), 0.0)

def find_wrf_files(model_key: str = "icon") -> list[Path]:
    model_key = (model_key or "icon").lower()
    output_dir = WRF_MODEL_OUTPUTS.get(model_key, WRF_MODEL_OUTPUTS["icon"])
    candidates = sorted(path for path in output_dir.glob("wrfout_d01_*") if path.is_file())
    if not candidates: raise FileNotFoundError(f"Nenhum arquivo wrfout encontrado em {output_dir}. Execute a simulacao {model_key.upper()} + WRF Sul 4 km primeiro.")
    runs: dict[str, list[Path]] = {}
    for path in candidates:
        match = re.search(r"wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[-:](\d{2})[-:](\d{2})", path.name)
        if not match: continue
        runs.setdefault(match.group(1), []).append(path)
    if not runs: return candidates
    latest_run = max(runs)
    def forecast_time(path: Path) -> tuple[int, int, int]:
        match = re.search(r"wrfout_d01_\d{4}-\d{2}-\d{2}_(\d{2})[-:](\d{2})[-:](\d{2})", path.name)
        if not match: return (0, 0, 0)
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    files = sorted(runs[latest_run], key=forecast_time)
    return files

def gfs_run_key(data_dir: Path) -> tuple[str, str]:
    run_info = data_dir / "run_info.env"
    run_date, run_cycle = "", ""
    if run_info.exists():
        for line in run_info.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("RUN_DATE="): run_date = line.split("=", 1)[1].strip()
            elif line.startswith("RUN_CYCLE="): run_cycle = line.split("=", 1)[1].strip()
    return (run_date, run_cycle)

def find_gfs_file(hours: int) -> Path:
    def valid_gfs_file(path: Path) -> bool: return path.is_file() and path.stat().st_size > 0 and re.search(r"gfs.t\d{2}z.pgrb2.0p25.f\d{3}$", path.name) is not None
    available_dirs = [data_dir for data_dir in GFS_DATA_DIRS if data_dir.exists()]
    search_dirs = sorted(available_dirs, key=gfs_run_key, reverse=True)
    files = [path for data_dir in search_dirs for path in data_dir.glob("gfs.t*z.pgrb2.0p25.f*") if valid_gfs_file(path)]
    if not files: raise FileNotFoundError(f"Nenhum arquivo GFS encontrado em {', '.join(str(path) for path in GFS_DATA_DIRS)}. Execute o download do GFS primeiro.")
    def forecast_hour(path: Path) -> int:
        suffix = path.name.rsplit(".f", 1)[-1]
        try: return int(suffix)
        except ValueError: return 0
    return min(files, key=lambda path: abs(forecast_hour(path) - hours))

def get_wrf_variable(ncfile_list: list[Any], var_name: str, timeidx: int) -> Any:
    from wrf import getvar
    try: return getvar(ncfile_list, var_name, timeidx=timeidx)
    except Exception as exc:
        print(f"AVISO: falha ao extrair variavel '{var_name}' do WRF: {exc}")
        return None



def _model_remote_key(model_key: str) -> str:
    key = (model_key or "gfs").lower().strip()
    if key not in MODEL_REMOTE_BASES:
        raise ValueError(f"Modelo remoto invalido: {model_key}")
    return key


def _wrf_remote_metadata(model_key: str = "gfs") -> dict[str, Any]:
    key = _model_remote_key(model_key)
    now = time.monotonic()
    cache = model_remote_cache[key]
    cached = cache.get("metadata")
    if isinstance(cached, dict) and now - float(cached.get("saved_at", 0.0)) < MODEL_REMOTE_CACHE_SECONDS:
        data = cached.get("data")
        if isinstance(data, dict):
            return data

    with model_remote_lock:
        cached = cache.get("metadata")
        if isinstance(cached, dict) and now - float(cached.get("saved_at", 0.0)) < MODEL_REMOTE_CACHE_SECONDS:
            data = cached.get("data")
            if isinstance(data, dict):
                return data

        url = f"{MODEL_REMOTE_BASES[key]}/metadata.json?ts={int(time.time() // 60)}"
        response = requests.get(
            url,
            headers={"User-Agent": "SideralMeteorologia/2.0", "Accept": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        allowed_schemas = {"sideral-wrf-metadata-v1", "sideral-model-metadata-v1"}
        if not isinstance(data, dict) or data.get("schema") not in allowed_schemas:
            raise RuntimeError(f"Metadata remoto de {key} em formato inesperado.")
        if str(data.get("model") or key).lower() != key:
            raise RuntimeError(f"Metadata remoto pertence a outro modelo: {data.get('model')}")
        frames = data.get("frames")
        if not isinstance(frames, list) or not frames:
            raise RuntimeError(f"Metadata remoto de {key} nao contem quadros.")
        cache["metadata"] = {"saved_at": now, "data": data}
        return data


def _wrf_remote_frame(model_key: str, filename: str) -> dict[str, Any]:
    key = _model_remote_key(model_key)
    safe_name = str(filename or "")
    if not re.fullmatch(rf"{re.escape(key)}/f\d{{3}}\.json\.gz", safe_name):
        raise ValueError(f"Nome de quadro remoto invalido para {key}: {safe_name}")

    now = time.monotonic()
    frames_cache = model_remote_cache[key].setdefault("frames", {})
    cached = frames_cache.get(safe_name)
    if isinstance(cached, dict) and now - float(cached.get("saved_at", 0.0)) < MODEL_REMOTE_CACHE_SECONDS:
        data = cached.get("data")
        if isinstance(data, dict):
            return data

    url = f"{MODEL_REMOTE_BASES[key]}/{safe_name}?ts={int(time.time() // 60)}"
    response = requests.get(
        url,
        headers={"User-Agent": "SideralMeteorologia/2.0", "Accept": "application/gzip,application/octet-stream"},
        timeout=35,
    )
    response.raise_for_status()
    try:
        decoded = zlib.decompress(response.content, 16 + zlib.MAX_WBITS)
        data = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Falha ao descompactar quadro remoto {key}: {exc}") from exc

    if not isinstance(data, dict) or data.get("schema") not in {"sideral-wrf-grid-v1", "sideral-model-grid-v1"}:
        raise RuntimeError(f"Quadro remoto {key} em formato inesperado.")
    if str(data.get("model") or key).lower() != key:
        raise RuntimeError(f"Quadro remoto pertence a outro modelo: {data.get('model')}")

    frames_cache[safe_name] = {"saved_at": now, "data": data}
    while len(frames_cache) > 40:
        frames_cache.pop(next(iter(frames_cache)))
    return data


def build_remote_wrf_cells(
    bounds: dict[str, float],
    hours: int,
    grid_x: int,
    grid_y: int,
    model_key: str = "gfs",
) -> dict[str, Any]:
    key = _model_remote_key(model_key)
    metadata = _wrf_remote_metadata(key)
    frames = metadata.get("frames") or []
    requested_hour = max(0, int(hours))
    frame_index = min(
        range(len(frames)),
        key=lambda index: abs(int(frames[index].get("forecastHour") or 0) - requested_hour),
    )
    frame_meta = frames[frame_index]
    frame = _wrf_remote_frame(key, str(frame_meta.get("file") or ""))

    source_bounds = frame.get("bounds") or {}
    margin = 0.20
    try:
        if (
            bounds["south"] < float(source_bounds["south"]) - margin
            or bounds["north"] > float(source_bounds["north"]) + margin
            or bounds["west"] < float(source_bounds["west"]) - margin
            or bounds["east"] > float(source_bounds["east"]) + margin
        ):
            raise WRFDomainError(
                f"Os dados {key.upper()} publicados cobrem apenas "
                f"{source_bounds.get('south')}..{source_bounds.get('north')} lat / "
                f"{source_bounds.get('west')}..{source_bounds.get('east')} lon."
            )
    except KeyError as exc:
        raise RuntimeError(f"Bounds ausentes no quadro remoto {key}.") from exc

    source_grid_x = int(frame.get("gridX") or 0)
    source_grid_y = int(frame.get("gridY") or 0)
    fields = frame.get("fields")
    if source_grid_x < 1 or source_grid_y < 1 or not isinstance(fields, dict):
        raise RuntimeError(f"Grade remota {key} invalida.")

    expected = source_grid_x * source_grid_y
    latitudes = fields.get("lat")
    longitudes = fields.get("lon")
    if (
        not isinstance(latitudes, list)
        or not isinstance(longitudes, list)
        or len(latitudes) != expected
        or len(longitudes) != expected
    ):
        raise RuntimeError(f"Coordenadas remotas {key} incompletas.")

    row_hits: list[int] = []
    col_hits: list[int] = []
    for row in range(source_grid_y):
        base = row * source_grid_x
        if any(
            bounds["south"] <= float(latitudes[base + col]) <= bounds["north"]
            and bounds["west"] <= float(longitudes[base + col]) <= bounds["east"]
            for col in range(source_grid_x)
        ):
            row_hits.append(row)
    for col in range(source_grid_x):
        if any(
            bounds["south"] <= float(latitudes[row * source_grid_x + col]) <= bounds["north"]
            and bounds["west"] <= float(longitudes[row * source_grid_x + col]) <= bounds["east"]
            for row in range(source_grid_y)
        ):
            col_hits.append(col)

    if not row_hits or not col_hits:
        raise WRFDomainError(f"A area solicitada nao cruza a grade remota {key}.")

    row_start, row_end = min(row_hits), max(row_hits)
    col_start, col_end = min(col_hits), max(col_hits)
    out_grid_y = row_end - row_start + 1
    out_grid_x = col_end - col_start + 1

    field_names = (
        "reflectivity", "precipitation", "windSpeed", "windDirection",
        "bulkShear", "vorticity850", "temperature", "humidity", "mucape", "waterVapor",
    )
    for name in field_names:
        values = fields.get(name)
        if not isinstance(values, list) or len(values) != expected:
            raise RuntimeError(f"Campo remoto {key} incompleto: {name}.")

    cells: list[dict[str, float]] = []
    for row in range(row_start, row_end + 1):
        base = row * source_grid_x
        for col in range(col_start, col_end + 1):
            idx = base + col
            cells.append({
                "lat": float(latitudes[idx]),
                "lon": float(longitudes[idx]),
                "reflectivity": max(0.0, float(fields["reflectivity"][idx])),
                "precipitation": max(0.0, float(fields["precipitation"][idx])),
                "cloudCover": 0.0,
                "windSpeed": float(fields["windSpeed"][idx]),
                "windDirection": float(fields["windDirection"][idx]),
                "bulkShear": float(fields["bulkShear"][idx]),
                "vorticity850": float(fields["vorticity850"][idx]),
                "temperature": float(fields["temperature"][idx]),
                "humidity": float(fields["humidity"][idx]),
                "mucape": float(fields["mucape"][idx]),
                "waterVapor": float(fields["waterVapor"][idx]),
            })

    available_frames = [
        {
            "index": index,
            "validTime": item.get("validTime"),
            "localValidTime": item.get("localValidTime"),
            "forecastHour": item.get("forecastHour"),
        }
        for index, item in enumerate(frames)
    ]
    default_capabilities = {
        "reflectivity": key == "gfs",
        "precipitation": True,
        "wind": True,
        "temperature": True,
        "humidity": True,
        "mucape": True,
        "bulkShear": key == "gfs",
        "vorticity850": key == "gfs",
        "waterVapor": key in {"gfs", "ecmwf"},
    }
    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = default_capabilities

    return {
        "cells": cells,
        "gridX": out_grid_x,
        "gridY": out_grid_y,
        "source": str(frame.get("source") or metadata.get("provider") or key.upper()),
        "model": key,
        "nativeGrid": False,
        "remoteGrid": True,
        "resolutionKm": metadata.get("resolutionKm"),
        "runDate": metadata.get("runDate"),
        "runCycle": metadata.get("runCycle"),
        "initTime": metadata.get("initTime"),
        "forecastHour": frame.get("forecastHour"),
        "frameIndex": frame_index,
        "frameCount": len(frames),
        "validTime": frame.get("validTime"),
        "availableFrames": available_frames,
        "capabilities": capabilities,
        "forecastLocalDate": metadata.get("forecastLocalDate"),
        "timezone": metadata.get("timezone"),
        "scope": metadata.get("scope"),
        "temporalResolutionMinutes": metadata.get("temporalResolutionMinutes"),
        "note": metadata.get("note"),
    }

def build_wrf_cells(bounds: dict[str, float], hours: int, grid_x: int, grid_y: int, model_key: str = "icon") -> list[dict[str, float]]:
    try: import numpy as np; import xarray as xr
    except ImportError as exc: raise RuntimeError("Dependencias WRF ausentes. Instale netCDF4, xarray e numpy.") from exc
    wrf_files = find_wrf_files(model_key)
    file_idx = min(max(0, int(hours)), len(wrf_files) - 1)
    prev_idx = max(0, file_idx - 1)
    def frame_valid_time(path: Path) -> str:
        match = re.search(r"wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[-:](\d{2})[-:](\d{2})", path.name)
        if not match: return path.name
        return f"{match.group(1)}T{match.group(2)}:{match.group(3)}:{match.group(4)}Z"
    dataset = xr.open_dataset(wrf_files[file_idx], engine="netcdf4")
    prev_dataset = xr.open_dataset(wrf_files[prev_idx], engine="netcdf4")
    try:
        def field(name: str) -> Any:
            if name not in dataset: raise RuntimeError(f"Variavel WRF ausente: {name}.")
            return dataset[name].isel(Time=0).to_numpy()
        lats = field("XLAT"); lons = field("XLONG")
        wrf_south, wrf_north = float(np.nanmin(lats)), float(np.nanmax(lats))
        wrf_west, wrf_east = float(np.nanmin(lons)), float(np.nanmax(lons))
        margin = 0.05
        if bounds["south"] < wrf_south - margin or bounds["north"] > wrf_north + margin or bounds["west"] < wrf_west - margin or bounds["east"] > wrf_east + margin:
            raise WRFDomainError(f"Os arquivos WRF atuais ainda cobrem apenas {wrf_south:.2f}..{wrf_north:.2f} lat / {wrf_west:.2f}..{wrf_east:.2f} lon.")
        u10 = field("U10"); v10 = field("V10"); t2m = field("T2") - 273.15; q2 = field("Q2"); psfc = field("PSFC")
        rain = field("RAINC") + field("RAINNC")
        prev_rain = prev_dataset["RAINC"].isel(Time=0).to_numpy() + prev_dataset["RAINNC"].isel(Time=0).to_numpy()
        precip_rate = np.maximum(0.0, rain - prev_rain)
        reflectivity_source = "REFL_10CM" if "REFL_10CM" in dataset else "hydrometeors"
        if "REFL_10CM" in dataset:
            reflectivity = np.maximum(0.0, np.nanmax(dataset["REFL_10CM"].isel(Time=0).to_numpy(), axis=0))
            approx_reflectivity = approximate_reflectivity_dbz(dataset, t2m, precip_rate)
            if np.nanmax(reflectivity) < 20.0 and np.nanmax(approx_reflectivity) > np.nanmax(reflectivity):
                reflectivity_source = "REFL_10CM+hydrometeors_approx"; reflectivity = np.maximum(reflectivity, approx_reflectivity)
        else:
            reflectivity = approximate_reflectivity_dbz(dataset, t2m, precip_rate)
            if np.nanmax(reflectivity) < 1.0: reflectivity_source = "precip_rate"; reflectivity = np.vectorize(precipitation_to_dbz)(precip_rate)
        temp_for_es = np.maximum(-80.0, t2m)
        es = 6.112 * np.exp((17.67 * temp_for_es) / (temp_for_es + 243.5))
        e = (q2 * psfc / 100.0) / (0.622 + q2)
        rh2 = np.clip((e / es) * 100.0, 0.0, 100.0)
        pressure = (field("P") + field("PB")) / 100.0
        qvapor = field("QVAPOR")
        water_vapor = np.maximum(0.0, -np.trapezoid(qvapor, pressure, axis=0) / 9.81)
        u_mass = 0.5 * (field("U")[:, :, :-1] + field("U")[:, :, 1:])
        v_mass = 0.5 * (field("V")[:, :-1, :] + field("V")[:, 1:, :])
        level_500 = np.nanargmin(np.abs(pressure - 500.0), axis=0)
        rows, cols = np.indices(t2m.shape)
        u500 = u_mass[level_500, rows, cols]; v500 = v_mass[level_500, rows, cols]
        dx_m = float(getattr(dataset, "DX", 3000.0)); dy_m = float(getattr(dataset, "DY", 3000.0))
        dvdx = np.gradient(v10, dx_m, axis=1); dudy = np.gradient(u10, dy_m, axis=0)
        vort850 = dvdx - dudy
        domain_mask = (lats >= bounds["south"]) & (lats <= bounds["north"]) & (lons >= bounds["west"]) & (lons <= bounds["east"])
        selected_rows = np.where(np.any(domain_mask, axis=1))[0]; selected_cols = np.where(np.any(domain_mask, axis=0))[0]
        if selected_rows.size == 0 or selected_cols.size == 0: raise WRFDomainError("A area solicitada nao cruza a grade nativa do WRF.")
        row_slice = slice(int(selected_rows[0]), int(selected_rows[-1]) + 1)
        col_slice = slice(int(selected_cols[0]), int(selected_cols[-1]) + 1)
        lats_native = lats[row_slice, col_slice]; lons_native = lons[row_slice, col_slice]
        refl_interp = np.nan_to_num(reflectivity[row_slice, col_slice], nan=0.0)
        u10_interp = np.nan_to_num(u10[row_slice, col_slice], nan=0.0); v10_interp = np.nan_to_num(v10[row_slice, col_slice], nan=0.0)
        precip_interp = np.nan_to_num(precip_rate[row_slice, col_slice], nan=0.0)
        t2m_interp = np.nan_to_num(t2m[row_slice, col_slice], nan=0.0); rh2_interp = np.nan_to_num(rh2[row_slice, col_slice], nan=0.0)
        u500_interp = np.nan_to_num(u500[row_slice, col_slice], nan=0.0); v500_interp = np.nan_to_num(v500[row_slice, col_slice], nan=0.0)
        vort850_interp = np.nan_to_num(vort850[row_slice, col_slice], nan=0.0)
        water_vapor_interp = np.nan_to_num(water_vapor[row_slice, col_slice], nan=0.0)
        bulk_shear = np.sqrt((u500_interp - u10_interp) ** 2 + (v500_interp - v10_interp) ** 2)
        mucape = np.maximum(0.0, (t2m_interp - 20.0) * rh2_interp * 8.0)
        native_grid_y, native_grid_x = refl_interp.shape
        cells: list[dict[str, float]] = []
        for i in range(native_grid_y):
            for j in range(native_grid_x):
                cells.append({"lat": float(lats_native[i, j]), "lon": float(lons_native[i, j]), "reflectivity": max(0.0, float(refl_interp[i, j])), "precipitation": max(0.0, float(precip_interp[i, j])), "cloudCover": 0.0, "windSpeed": float(math.hypot(u10_interp[i, j], v10_interp[i, j]) * 3.6), "windDirection": float(wind_direction_deg(u10_interp[i, j], v10_interp[i, j])), "bulkShear": float(bulk_shear[i, j]), "vorticity850": float(vort850_interp[i, j]), "temperature": float(t2m_interp[i, j]), "humidity": float(rh2_interp[i, j]), "mucape": float(mucape[i, j]), "waterVapor": float(water_vapor_interp[i, j])})
        return {"cells": cells, "gridX": int(native_grid_x), "gridY": int(native_grid_y), "source": reflectivity_source, "model": model_key, "nativeGrid": True, "frameIndex": file_idx, "frameCount": len(wrf_files), "validTime": frame_valid_time(wrf_files[file_idx]), "availableFrames": [{"index": index, "validTime": frame_valid_time(path)} for index, path in enumerate(wrf_files)]}
    finally: dataset.close(); prev_dataset.close()

def build_gfs_cells(bounds: dict[str, float], hours: int, grid_x: int, grid_y: int) -> list[dict[str, float]]:
    try: import cfgrib; import numpy as np
    except ImportError as exc: raise RuntimeError("Dependencias GFS ausentes. Instale cfgrib e numpy para usar /api/gfs/cells.") from exc
    gfs_file = find_gfs_file(hours)
    lats_to_sample = np.linspace(bounds["south"], bounds["north"], grid_y)
    lons_to_sample = np.linspace(bounds["west"], bounds["east"], grid_x)
    def open_field(filter_by_keys: dict[str, Any]) -> Any: return cfgrib.open_dataset(str(gfs_file), filter_by_keys=filter_by_keys, indexpath="")
    def sample_regular_grid(data_array: Any, default: float) -> Any:
        longitude = ((data_array.longitude + 180) % 360) - 180
        data_array = data_array.assign_coords(longitude=longitude).sortby("longitude").sortby("latitude")
        source_lats = data_array.latitude.to_numpy(); source_lons = data_array.longitude.to_numpy()
        values = np.squeeze(data_array.to_numpy())
        lon_interpolated = np.vstack([np.interp(lons_to_sample, source_lons, row, left=np.nan, right=np.nan) for row in values])
        sampled = np.vstack([np.interp(lats_to_sample, source_lats, lon_interpolated[:, column], left=np.nan, right=np.nan) for column in range(lon_interpolated.shape[1])]).T
        return np.nan_to_num(sampled, nan=default)
    def sample_field(filter_candidates: list[dict[str, Any]], field_name: str | None = None, default: float = 0.0) -> Any:
        last_error: Exception | None = None
        for filter_by_keys in filter_candidates:
            dataset = None
            try:
                dataset = open_field(filter_by_keys)
                if field_name and field_name in dataset: data_array = dataset[field_name]
                else:
                    data_vars = list(dataset.data_vars)
                    if not data_vars: continue
                    data_array = dataset[data_vars[0]]
                return sample_regular_grid(data_array, default)
            except Exception as exc: last_error = exc
            finally:
                if dataset is not None: dataset.close()
        return np.full((grid_y, grid_x), default, dtype=float)
    reflectivity = sample_field([{"shortName": "refc", "typeOfLevel": "atmosphere"}, {"shortName": "refc"}, {"shortName": "refd"}], default=0.0)
    u10 = sample_field([{"shortName": "10u", "typeOfLevel": "heightAboveGround", "level": 10}], default=0.0)
    v10 = sample_field([{"shortName": "10v", "typeOfLevel": "heightAboveGround", "level": 10}], default=0.0)
    cells: list[dict[str, float]] = []
    for i in range(grid_y):
        for j in range(grid_x):
            wind_speed = float(math.hypot(u10[i, j], v10[i, j]) * 3.6)
            cells.append({"lat": float(lats_to_sample[i]), "lon": float(lons_to_sample[j]), "reflectivity": max(0.0, float(reflectivity[i, j])), "precipitation": 0.0, "cloudCover": 0.0, "windSpeed": wind_speed, "windDirection": float(wind_direction_deg(float(u10[i, j]), float(v10[i, j]))), "bulkShear": 0.0, "vorticity850": 0.0, "temperature": 0.0, "humidity": 0.0, "mucape": 0.0, "waterVapor": 0.0})
    return cells

def build_meteoblue_cells(bounds: dict[str, float], hours: int, grid_x: int, grid_y: int) -> list[dict[str, float]]:
    api_key = "4imCvOUtnMT3NjeA"
    response = requests.get("https://my.meteoblue.com/packages/basic-1h", params={"lat": (bounds["north"] + bounds["south"]) / 2, "lon": (bounds["east"] + bounds["west"]) / 2, "apikey": api_key, "tz": "UTC", "windspeed": "km/h", "forecast_days": 3}, timeout=20)
    response.raise_for_status()
    meteoblue_data = response.json()
    hourly_data = meteoblue_data.get("data_1h")
    if not hourly_data: raise RuntimeError(f"Resposta invalida da API Meteoblue: {meteoblue_data.get('error_message', 'Erro desconhecido')}")
    times = hourly_data.get("time", [])
    if not times: raise RuntimeError("Nenhum horario retornado pela API Meteoblue.")
    target_time = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=hours)
    closest_hour_index = min(range(len(times)), key=lambda index: abs((dt.datetime.strptime(times[index], "%Y-%m-%d %H:%M") - target_time).total_seconds()))
    def hour_value(field_name: str) -> float:
        values = hourly_data.get(field_name, [])
        return float(values[closest_hour_index] or 0.0) if closest_hour_index < len(values) else 0.0
    precipitation = hour_value("precipitation"); wind_speed = hour_value("windspeed_10m"); wind_direction = hour_value("winddirection_10m")
    reflectivity = precipitation_to_dbz(precipitation)
    lat_step = (bounds["north"] - bounds["south"]) / (grid_y - 1) if grid_y > 1 else 0.0
    lon_step = (bounds["east"] - bounds["west"]) / (grid_x - 1) if grid_x > 1 else 0.0
    cells: list[dict[str, float]] = []
    for i in range(grid_y):
        for j in range(grid_x):
            cells.append({"lat": bounds["south"] + i * lat_step, "lon": bounds["west"] + j * lon_step, "reflectivity": reflectivity, "precipitation": precipitation, "cloudCover": 0.0, "windSpeed": wind_speed, "windDirection": wind_direction, "bulkShear": 0.0, "vorticity850": 0.0, "temperature": 0.0, "humidity": 0.0, "mucape": 0.0, "waterVapor": 0.0})
    return cells


def _ecmwf_runtime_modules() -> dict[str, Any]:
    """Dependências necessárias ao Skew-T. Dados ECMWF vêm via Open-Meteo."""
    try:
        import numpy as np
        from sharppy.sharptab import profile as shp_profile
    except ImportError as exc:
        raise RuntimeError(
            "Dependências do Skew-T ausentes. Instale numpy e SHARPpy."
        ) from exc
    return {
        "np": np,
        "shp_profile": shp_profile,
    }


def _ecmwf_api_client(modules: dict[str, Any]) -> Any:
    api_url = os.environ.get("ECMWF_API_URL", "https://api.ecmwf.int/v1").strip()
    api_key = os.environ.get("ECMWF_API_KEY", "").strip()
    api_email = os.environ.get("ECMWF_API_EMAIL", "").strip()
    if not api_key or not api_email:
        raise RuntimeError(
            "Credenciais ECMWF não configuradas no servidor."
        )
    return modules["ECMWFService"](
        "mars",
        url=api_url,
        key=api_key,
        email=api_email,
    )



def _sanitize_server_error(value: Any) -> str:
    """Remove e-mail, chaves e credenciais antes de registrar erros no Render."""
    text = str(value or "")
    for env_name in ("ECMWF_API_KEY", "ECMWF_API_EMAIL"):
        secret = os.environ.get(env_name, "").strip()
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "[EMAIL-REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(key|token|secret|password)\s*[:=]\s*['\"]?[^,'\"\s}]+",
        r"\1=[REDACTED]",
        text,
    )
    return text[:1200]


def _log_sounding_error(prefix: str, exc: BaseException) -> None:
    safe = _sanitize_server_error(exc)
    print(f"[SKEWT] {prefix}: {type(exc).__name__}: {safe}")

def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or abs(number) > 1.0e20:
        return None
    return number


def _dewpoint_from_temperature_rh(temp_c: float, rh_pct: float) -> float | None:
    """Magnus: transforma temperatura e UR do IFS em ponto de orvalho para o SHARPpy."""
    if not math.isfinite(temp_c) or not math.isfinite(rh_pct) or rh_pct <= 0:
        return None
    rh = max(0.1, min(100.0, rh_pct))
    a, b = 17.625, 243.04
    gamma = math.log(rh / 100.0) + (a * temp_c) / (b + temp_c)
    dewpoint = (b * gamma) / (a - gamma)
    return min(temp_c, dewpoint)


def _ecmwf_cleanup_cache() -> None:
    try:
        ECMWF_SOUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 6 * 60 * 60
        files = sorted(ECMWF_SOUNDING_CACHE_DIR.glob("*.grib"), key=lambda p: p.stat().st_mtime, reverse=True)
        for index, path in enumerate(files):
            if path.stat().st_mtime < cutoff or index >= 24:
                try:
                    path.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _ecmwf_run_candidates(run_requested: str) -> list[dt.datetime]:
    now = dt.datetime.now(dt.timezone.utc)
    if run_requested != "latest":
        hour = int(run_requested)
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate > now:
            candidate -= dt.timedelta(days=1)
        return [candidate]

    # Para "latest", evita a rodada que ainda pode estar em disseminação e mantém fallbacks.
    reference = now - dt.timedelta(hours=7)
    base_hour = (reference.hour // 6) * 6
    first = reference.replace(hour=base_hour, minute=0, second=0, microsecond=0)
    return [first - dt.timedelta(hours=6 * index) for index in range(4)]


def _ecmwf_cached_grib(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 512 and (time.time() - path.stat().st_mtime) < ECMWF_GRIB_CACHE_SECONDS
    except OSError:
        return False


def _ecmwf_point_tag(lat: float, lon: float) -> str:
    return f"{lat:+07.2f}_{lon:+08.2f}".replace("+", "p").replace("-", "m").replace(".", "d")


def _ecmwf_retrieve_grib(client: Any, run_dt: dt.datetime, fh: int, lat: float, lon: float) -> tuple[Path, Path, Path]:
    ECMWF_SOUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = run_dt.strftime("%Y%m%d_%H")
    point_tag = _ecmwf_point_tag(lat, lon)
    pressure_path = ECMWF_SOUNDING_CACHE_DIR / f"ifs_g010_{stamp}_f{fh:03d}_{point_tag}_pl.grib"
    surface_path = ECMWF_SOUNDING_CACHE_DIR / f"ifs_g010_{stamp}_f{fh:03d}_{point_tag}_sfc.grib"
    orography_path = ECMWF_SOUNDING_CACHE_DIR / f"ifs_g010_{stamp}_{point_tag}_oro.grib"

    north = min(90.0, lat + 0.30)
    south = max(-90.0, lat - 0.30)
    west = max(-180.0, lon - 0.30)
    east = min(180.0, lon + 0.30)
    area = f"{north:.2f}/{west:.2f}/{south:.2f}/{east:.2f}"

    def execute_atomic(target: Path, request: dict[str, Any]) -> None:
        if _ecmwf_cached_grib(target):
            return
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        client.execute(request, str(tmp))
        if not tmp.exists() or tmp.stat().st_size < 512:
            raise RuntimeError(f"ECMWF retornou arquivo vazio para {target.name}.")
        tmp.replace(target)

    common_fc = {
        "class": "od",
        "date": run_dt.strftime("%Y%m%d"),
        "expver": "1",
        "stream": "oper",
        "time": f"{run_dt.hour:02d}",
        "type": "fc",
        "step": str(fh),
        "grid": "0.1/0.1",
        "area": area,
    }

    with ecmwf_download_lock:
        execute_atomic(
            pressure_path,
            {
                **common_fc,
                "levtype": "pl",
                "levelist": "/".join(str(level) for level in ECMWF_PRESSURE_LEVELS),
                # T / U / V / RH / geopotential height / vertical velocity (omega, Pa/s).
                "param": "130.128/131.128/132.128/157.128/156.128/135.128",
            },
        )
        execute_atomic(
            surface_path,
            {
                **common_fc,
                "levtype": "sfc",
                # SP / 10U / 10V / 2T / 2D.
                "param": "134.128/165.128/166.128/167.128/168.128",
            },
        )
        execute_atomic(
            orography_path,
            {
                "class": "od",
                "date": run_dt.strftime("%Y%m%d"),
                "expver": "1",
                "stream": "oper",
                "time": f"{run_dt.hour:02d}",
                "type": "an",
                "levtype": "sfc",
                "param": "129.128",
                "grid": "0.1/0.1",
                "area": area,
            },
        )
        _ecmwf_cleanup_cache()
    return pressure_path, surface_path, orography_path


def _ecmwf_read_nearest_grib(path: Path, lat: float, lon: float, modules: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    new_from_file = modules["codes_grib_new_from_file"]
    codes_get = modules["codes_get"]
    find_nearest = modules["codes_grib_find_nearest"]
    release = modules["codes_release"]
    with path.open("rb") as stream:
        while True:
            gid = new_from_file(stream)
            if gid is None:
                break
            try:
                short_name = str(codes_get(gid, "shortName"))
                type_of_level = str(codes_get(gid, "typeOfLevel"))
                try:
                    level = _safe_float(codes_get(gid, "level"))
                except Exception:
                    level = None
                nearest = find_nearest(gid, lat, lon)[0]
                value = _safe_float(getattr(nearest, "value", None))
                nearest_lat = _safe_float(getattr(nearest, "lat", None))
                nearest_lon = _safe_float(getattr(nearest, "lon", None))
                if value is None:
                    continue
                if type_of_level == "isobaricInPa" and level is not None:
                    level /= 100.0
                fields.append({
                    "short_name": short_name,
                    "type_of_level": type_of_level,
                    "level": level,
                    "value": value,
                    "grid_lat": nearest_lat,
                    "grid_lon": nearest_lon,
                })
            finally:
                release(gid)
    return fields


def _sharppy_number(value: Any, np: Any) -> float | None:
    try:
        if np.ma.is_masked(value):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= -9000:
        return None
    return number


def _sharppy_vector_magnitude(value: Any, np: Any) -> float | None:
    try:
        if value is None or len(value) < 2:
            return None
        u = _sharppy_number(value[0], np)
        v = _sharppy_number(value[1], np)
        return math.hypot(u, v) if u is not None and v is not None else None
    except Exception:
        return None


def _parcel_json(parcel: Any, np: Any) -> dict[str, Any]:
    def n(name: str) -> float | None:
        return _sharppy_number(getattr(parcel, name, None), np)

    pressure: list[float] = []
    temperature: list[float] = []
    try:
        ptrace = getattr(parcel, "ptrace", [])
        ttrace = getattr(parcel, "ttrace", [])
        for p, t in zip(ptrace, ttrace):
            pnum = _sharppy_number(p, np)
            tnum = _sharppy_number(t, np)
            if pnum is not None and tnum is not None:
                pressure.append(round(pnum, 2))
                temperature.append(round(tnum, 2))
    except Exception:
        pass
    return {
        "cape": n("bplus"),
        "cin": n("bminus"),
        "cape_3km": n("b3km"),
        "cape_6km": n("b6km"),
        "cape_to_freezing": n("bfzl"),
        "lcl": n("lclhght"),
        "lfc": n("lfchght"),
        "el": n("elhght"),
        "lcl_pressure": n("lclpres"),
        "lfc_pressure": n("lfcpres"),
        "el_pressure": n("elpres"),
        "freezing_height": n("hght0c"),
        "minus10_height": n("hghtm10c"),
        "minus20_height": n("hghtm20c"),
        "minus30_height": n("hghtm30c"),
        "li5": n("li5"),
        "li3": n("li3"),
        "brn": n("brn"),
        "brn_shear": n("brnshear"),
        "brn_u": n("brnu"),
        "brn_v": n("brnv"),
        "cap_strength": n("cap"),
        "source_pressure": n("pres"),
        "source_temperature": n("tmpc"),
        "source_dewpoint": n("dwpc"),
        "trace": {"pressure": pressure, "temperature": temperature},
    }


def _ecmwf_build_sounding_payload(
    lat: float,
    lon: float,
    run_dt: dt.datetime,
    fh: int,
    modules: dict[str, Any],
) -> dict[str, Any]:
    """
    Obtém um ponto do ECMWF IFS 0.25° via Open-Meteo Single Runs e
    processa o perfil com SHARPpy. Não usa MARS e não requer chave ECMWF.
    """
    np = modules["np"]
    shp_profile = modules["shp_profile"]

    levels = ECMWF_PRESSURE_LEVELS
    hourly_vars = [
        "temperature_2m",
        "dew_point_2m",
        "surface_pressure",
        "wind_speed_10m",
        "wind_direction_10m",
    ]
    for level in levels:
        hourly_vars.extend([
            f"temperature_{level}hPa",
            f"relative_humidity_{level}hPa",
            f"wind_speed_{level}hPa",
            f"wind_direction_{level}hPa",
            f"geopotential_height_{level}hPa",
        ])

    params = {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "run": run_dt.strftime("%Y-%m-%dT%H:00"),
        "models": "ecmwf_ifs025",
        "forecast_hours": str(max(6, fh + 6)),
        "timezone": "GMT",
        "temporal_resolution": "native",
        "cell_selection": "nearest",
        "elevation": "nan",
        "wind_speed_unit": "kn",
        "hourly": ",".join(hourly_vars),
    }

    response = requests.get(
        "https://single-runs-api.open-meteo.com/v1/forecast",
        params=params,
        headers={"User-Agent": "SideralMeteorologia/1.0"},
        timeout=45,
    )
    if response.status_code != 200:
        try:
            reason = response.json().get("reason")
        except Exception:
            reason = None
        raise RuntimeError(
            f"Open-Meteo ECMWF respondeu HTTP {response.status_code}"
            + (f": {reason}" if reason else "")
        )

    data = response.json()
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    target_dt = run_dt + dt.timedelta(hours=fh)
    target_iso = target_dt.strftime("%Y-%m-%dT%H:%M")

    try:
        idx = times.index(target_iso)
    except ValueError:
        # Alguns retornos podem omitir minutos (:00).
        short_target = target_dt.strftime("%Y-%m-%dT%H")
        idx = next(
            (i for i, value in enumerate(times) if str(value).startswith(short_target)),
            -1,
        )
    if idx < 0:
        raise RuntimeError(
            f"Open-Meteo não retornou o horário válido F{fh:03d} para a rodada solicitada."
        )

    def hv(name: str) -> float | None:
        values = hourly.get(name)
        if not isinstance(values, list) or idx >= len(values):
            return None
        return _safe_float(values[idx])

    surface_pressure = hv("surface_pressure")
    surface_temp = hv("temperature_2m")
    surface_dewpoint = hv("dew_point_2m")
    surface_wind_speed = hv("wind_speed_10m")
    surface_wind_dir = hv("wind_direction_10m")
    surface_height = _safe_float(data.get("elevation"))
    grid_lat = _safe_float(data.get("latitude"))
    grid_lon = _safe_float(data.get("longitude"))

    if None in (
        surface_pressure,
        surface_temp,
        surface_dewpoint,
        surface_wind_speed,
        surface_wind_dir,
    ):
        raise RuntimeError(f"Open-Meteo não retornou todos os campos de superfície necessários em F{fh:03d}.")

    if surface_height is None:
        surface_height = 0.0

    def uv_from_dir_speed(direction_deg: float, speed_kt: float) -> tuple[float, float]:
        angle = math.radians(direction_deg)
        return (
            -speed_kt * math.sin(angle),
            -speed_kt * math.cos(angle),
        )

    surface_u, surface_v = uv_from_dir_speed(surface_wind_dir, surface_wind_speed)

    points: list[dict[str, float]] = [{
        "pressure": float(surface_pressure),
        "height": float(surface_height),
        "temperature": float(surface_temp),
        "dewpoint": min(float(surface_temp), float(surface_dewpoint)),
        "u": float(surface_u),
        "v": float(surface_v),
        "omega": math.nan,
    }]

    for level in levels:
        if level > float(surface_pressure) + 0.5:
            continue

        temp_c = hv(f"temperature_{level}hPa")
        rh = hv(f"relative_humidity_{level}hPa")
        wind_speed = hv(f"wind_speed_{level}hPa")
        wind_dir = hv(f"wind_direction_{level}hPa")
        gh = hv(f"geopotential_height_{level}hPa")
        vertical_ms = None

        if None in (temp_c, rh, wind_speed, wind_dir, gh):
            continue

        dewpoint_c = _dewpoint_from_temperature_rh(float(temp_c), float(rh))
        if dewpoint_c is None:
            continue

        u_kt, v_kt = uv_from_dir_speed(float(wind_dir), float(wind_speed))

        omega_pa_s = math.nan
        if vertical_ms is not None:
            # Open-Meteo fornece velocidade vertical geométrica (m/s).
            # Converte de volta para omega (Pa/s) para o SHARPpy:
            # omega = -rho * g * w, usando T como aproximação de Tv.
            t_k = float(temp_c) + 273.15
            if t_k > 0:
                rho = (float(level) * 100.0) / (287.05 * t_k)
                omega_pa_s = -rho * 9.80665 * float(vertical_ms)

        points.append({
            "pressure": float(level),
            "height": float(gh),
            "temperature": float(temp_c),
            "dewpoint": float(dewpoint_c),
            "u": float(u_kt),
            "v": float(v_kt),
            "omega": float(omega_pa_s),
        })

    points.sort(key=lambda item: item["pressure"], reverse=True)
    qc_points: list[dict[str, float]] = []
    last_height = -1.0e9
    for point in points:
        if qc_points and abs(point["pressure"] - qc_points[-1]["pressure"]) < 0.75:
            if point["height"] < qc_points[-1]["height"]:
                qc_points[-1] = point
            continue
        if point["height"] <= last_height:
            continue
        qc_points.append(point)
        last_height = point["height"]
    points = qc_points

    if len(points) < 8 or points[-1]["pressure"] > 300:
        raise RuntimeError("Perfil ECMWF insuficiente para o SHARPpy.")

    pres = np.array([p["pressure"] for p in points], dtype=float)
    hght = np.array([p["height"] for p in points], dtype=float)
    tmpc = np.array([p["temperature"] for p in points], dtype=float)
    dwpc = np.array([p["dewpoint"] for p in points], dtype=float)
    u = np.array([p["u"] for p in points], dtype=float)
    v = np.array([p["v"] for p in points], dtype=float)
    omega = np.array([p.get("omega", math.nan) for p in points], dtype=float)

    # Apenas o núcleo de cálculo do SHARPpy é usado; nenhuma GUI/Qt é iniciada.
    profile_kwargs = {
        "profile": "convective",
        "pres": pres,
        "hght": hght,
        "tmpc": tmpc,
        "dwpc": dwpc,
        "u": u,
        "v": v,
        "latitude": float(lat),
        "date": run_dt + dt.timedelta(hours=fh),
        "location": "ECMWF",
        "strictQC": False,
    }
    # O omega oficial do ECMWF permite OPRH/DGZ e diagnósticos de inverno reais.
    omega_masked = np.ma.masked_invalid(omega)
    if int(np.ma.count(omega_masked)) >= 2:
        profile_kwargs["omeg"] = omega_masked
    prof = shp_profile.create_profile(**profile_kwargs)

    sb = _parcel_json(prof.sfcpcl, np)
    ml = _parcel_json(prof.mlpcl, np)
    fcst = _parcel_json(prof.fcstpcl, np)
    mu = _parcel_json(prof.mupcl, np)
    eff = _parcel_json(getattr(prof, "effpcl", prof.sfcpcl), np)
    shear01 = _sharppy_vector_magnitude(getattr(prof, "sfc_1km_shear", None), np)
    shear03 = _sharppy_vector_magnitude(getattr(prof, "sfc_3km_shear", None), np)
    shear06 = _sharppy_vector_magnitude(getattr(prof, "sfc_6km_shear", None), np)

    if lat < 0:
        srh01_raw = _sharppy_number(getattr(prof, "left_srh1km", [None])[0], np)
        srh03_raw = _sharppy_number(getattr(prof, "left_srh3km", [None])[0], np)
        srh01 = -srh01_raw if srh01_raw is not None else None
        srh03 = -srh03_raw if srh03_raw is not None else None
        motion = getattr(prof, "srwind", [None, None, None, None])[2:4]
    else:
        srh01 = _sharppy_number(getattr(prof, "right_srh1km", [None])[0], np)
        srh03 = _sharppy_number(getattr(prof, "right_srh3km", [None])[0], np)
        motion = getattr(prof, "srwind", [None, None, None, None])[0:2]

    storm_u = _sharppy_number(motion[0], np) if len(motion) > 0 else None
    storm_v = _sharppy_number(motion[1], np) if len(motion) > 1 else None
    storm_speed = math.hypot(storm_u, storm_v) if storm_u is not None and storm_v is not None else None
    storm_dir = wind_direction_deg(storm_u, storm_v) if storm_u is not None and storm_v is not None else None

    pwat_in = _sharppy_number(getattr(prof, "pwat", None), np)
    stp = _sharppy_number(getattr(prof, "stp_cin", None), np)
    stp_fixed = _sharppy_number(getattr(prof, "stp_fixed", None), np)
    scp = _sharppy_number(getattr(prof, "scp", None), np)
    ship = _sharppy_number(getattr(prof, "ship", None), np)
    sherb = _sharppy_number(getattr(prof, "sherbe", None), np)
    ehi01 = None
    ehi03 = None
    if mu.get("cape") is not None:
        if srh01 is not None:
            ehi01 = (float(mu["cape"]) * float(srh01)) / 160000.0
        if srh03 is not None:
            ehi03 = (float(mu["cape"]) * float(srh03)) / 160000.0

    def seq_number(value: Any, index: int) -> float | None:
        try:
            return _sharppy_number(value[index], np)
        except Exception:
            return None

    def dir_speed_from_uv(u_value: Any, v_value: Any) -> dict[str, float | None]:
        u_num = _sharppy_number(u_value, np)
        v_num = _sharppy_number(v_value, np)
        if u_num is None or v_num is None:
            return {"u": None, "v": None, "direction": None, "speed": None}
        return {
            "u": u_num,
            "v": v_num,
            "direction": wind_direction_deg(u_num, v_num),
            "speed": math.hypot(u_num, v_num),
        }

    def dir_speed_pair(value: Any) -> dict[str, float | None]:
        direction = seq_number(value, 0)
        speed = seq_number(value, 1)
        if direction is None or speed is None:
            return {"direction": None, "speed": None, "u": None, "v": None}
        rad = math.radians(direction)
        return {
            "direction": direction,
            "speed": speed,
            "u": -speed * math.sin(rad),
            "v": -speed * math.cos(rad),
        }

    bunkers = getattr(prof, "bunkers", [None, None, None, None])
    bunkers_rm = dir_speed_from_uv(seq_number(bunkers, 0), seq_number(bunkers, 1))
    bunkers_lm = dir_speed_from_uv(seq_number(bunkers, 2), seq_number(bunkers, 3))

    corfidi = getattr(prof, "upshear_downshear", [None, None, None, None])
    corfidi_up = dir_speed_from_uv(seq_number(corfidi, 0), seq_number(corfidi, 1))
    corfidi_down = dir_speed_from_uv(seq_number(corfidi, 2), seq_number(corfidi, 3))

    mean01 = dir_speed_pair(getattr(prof, "mean_1km", [None, None]))
    mean03 = dir_speed_pair(getattr(prof, "mean_3km", [None, None]))
    mean06 = dir_speed_pair(getattr(prof, "mean_6km", [None, None]))
    mean08 = dir_speed_pair(getattr(prof, "mean_8km", [None, None]))
    mean_lcl_el = dir_speed_pair(getattr(prof, "mean_lcl_el", [None, None]))
    mean_eff_raw = getattr(prof, "mean_eff", [None, None])
    mean_ebw_raw = getattr(prof, "mean_ebw", [None, None])
    mean_eff = dir_speed_from_uv(seq_number(mean_eff_raw, 0), seq_number(mean_eff_raw, 1))
    mean_ebw = dir_speed_from_uv(seq_number(mean_ebw_raw, 0), seq_number(mean_ebw_raw, 1))

    if lat < 0:
        effective_srh_raw = seq_number(getattr(prof, "left_esrh", [None]), 0)
        effective_srh = -effective_srh_raw if effective_srh_raw is not None else None
        critical_angle = _sharppy_number(getattr(prof, "left_critical_angle", None), np)
        srw01 = dir_speed_pair(getattr(prof, "left_srw_1km", [None, None]))
        srw03 = dir_speed_pair(getattr(prof, "left_srw_3km", [None, None]))
        srw06 = dir_speed_pair(getattr(prof, "left_srw_6km", [None, None]))
        srw08 = dir_speed_pair(getattr(prof, "left_srw_8km", [None, None]))
        srw45 = dir_speed_pair(getattr(prof, "left_srw_4_5km", [None, None]))
        srw_lcl_el = dir_speed_pair(getattr(prof, "left_srw_lcl_el", [None, None]))
        srw_eff_raw = getattr(prof, "left_srw_eff", [None, None])
        srw_ebw_raw = getattr(prof, "left_srw_ebw", [None, None])
        selected_motion_name = "Bunkers LM (ciclônico SH)"
    else:
        effective_srh = seq_number(getattr(prof, "right_esrh", [None]), 0)
        critical_angle = _sharppy_number(getattr(prof, "right_critical_angle", None), np)
        srw01 = dir_speed_pair(getattr(prof, "right_srw_1km", [None, None]))
        srw03 = dir_speed_pair(getattr(prof, "right_srw_3km", [None, None]))
        srw06 = dir_speed_pair(getattr(prof, "right_srw_6km", [None, None]))
        srw08 = dir_speed_pair(getattr(prof, "right_srw_8km", [None, None]))
        srw45 = dir_speed_pair(getattr(prof, "right_srw_4_5km", [None, None]))
        srw_lcl_el = dir_speed_pair(getattr(prof, "right_srw_lcl_el", [None, None]))
        srw_eff_raw = getattr(prof, "right_srw_eff", [None, None])
        srw_ebw_raw = getattr(prof, "right_srw_ebw", [None, None])
        selected_motion_name = "Bunkers RM (ciclônico NH)"

    srw_eff = dir_speed_from_uv(seq_number(srw_eff_raw, 0), seq_number(srw_eff_raw, 1))
    srw_ebw = dir_speed_from_uv(seq_number(srw_ebw_raw, 0), seq_number(srw_ebw_raw, 1))
    ebwspd = _sharppy_number(getattr(prof, "ebwspd", None), np)
    effective_bottom = _sharppy_number(getattr(prof, "ebotm", None), np)
    effective_top = _sharppy_number(getattr(prof, "etopm", None), np)
    shear08 = _sharppy_vector_magnitude(getattr(prof, "sfc_8km_shear", None), np)
    shear09 = _sharppy_vector_magnitude(getattr(prof, "sfc_9km_shear", None), np)
    lcl_el_shear = _sharppy_vector_magnitude(getattr(prof, "lcl_el_shear", None), np)
    eff_shear = _sharppy_vector_magnitude(getattr(prof, "eff_shear", None), np)
    ebwd = _sharppy_vector_magnitude(getattr(prof, "ebwd", None), np)
    wind1km = dir_speed_pair(getattr(prof, "wind1km", [None, None]))
    wind6km = dir_speed_pair(getattr(prof, "wind6km", [None, None]))

    k_index = _sharppy_number(getattr(prof, "k_idx", None), np)
    totals_totals = _sharppy_number(getattr(prof, "totals_totals", None), np)
    lapse_03 = _sharppy_number(getattr(prof, "lapserate_3km", None), np)
    lapse_36 = _sharppy_number(getattr(prof, "lapserate_3_6km", None), np)
    lapse_850_500 = _sharppy_number(getattr(prof, "lapserate_850_500", None), np)
    lapse_700_500 = _sharppy_number(getattr(prof, "lapserate_700_500", None), np)
    max_lapse_26 = _sharppy_number(getattr(prof, "max_lapse_rate_2_6", None), np)
    conv_temp_f = _sharppy_number(getattr(prof, "convT", None), np)
    max_temp_f = _sharppy_number(getattr(prof, "maxT", None), np)
    mean_mixr = _sharppy_number(getattr(prof, "mean_mixr", None), np)
    low_rh = _sharppy_number(getattr(prof, "low_rh", None), np)
    mid_rh = _sharppy_number(getattr(prof, "mid_rh", None), np)
    dcape = _sharppy_number(getattr(prof, "dcape", None), np)
    drush_f = _sharppy_number(getattr(prof, "drush", None), np)
    tei = _sharppy_number(getattr(prof, "tei", None), np)
    esp = _sharppy_number(getattr(prof, "esp", None), np)
    mmp = _sharppy_number(getattr(prof, "mmp", None), np)
    wndg = _sharppy_number(getattr(prof, "wndg", None), np)
    sig_severe = _sharppy_number(getattr(prof, "sig_severe", None), np)
    mburst = _sharppy_number(getattr(prof, "mburst", None), np)

    dgz_pbot = _sharppy_number(getattr(prof, "dgz_pbot", None), np)
    dgz_ptop = _sharppy_number(getattr(prof, "dgz_ptop", None), np)
    dgz_meanrh = _sharppy_number(getattr(prof, "dgz_meanrh", None), np)
    dgz_pw_in = _sharppy_number(getattr(prof, "dgz_pw", None), np)
    dgz_meanq = _sharppy_number(getattr(prof, "dgz_meanq", None), np)
    dgz_meanomega = _sharppy_number(getattr(prof, "dgz_meanomeg", None), np)
    oprh = _sharppy_number(getattr(prof, "oprh", None), np)
    initial_phase_pressure = _sharppy_number(getattr(prof, "plevel", None), np)
    initial_phase_temp = _sharppy_number(getattr(prof, "tmp", None), np)
    initial_phase_raw = getattr(prof, "phase", None)
    initial_phase = None if initial_phase_raw is None or np.ma.is_masked(initial_phase_raw) else str(initial_phase_raw)
    initial_state_raw = getattr(prof, "st", None)
    initial_state = None if initial_state_raw is None or np.ma.is_masked(initial_state_raw) else str(initial_state_raw)
    tpos = _sharppy_number(getattr(prof, "tpos", None), np)
    tneg = _sharppy_number(getattr(prof, "tneg", None), np)
    ttop = _sharppy_number(getattr(prof, "ttop", None), np)
    tbot = _sharppy_number(getattr(prof, "tbot", None), np)
    wpos = _sharppy_number(getattr(prof, "wpos", None), np)
    wneg = _sharppy_number(getattr(prof, "wneg", None), np)
    wtop = _sharppy_number(getattr(prof, "wtop", None), np)
    wbot = _sharppy_number(getattr(prof, "wbot", None), np)
    precip_type_raw = getattr(prof, "precip_type", None)
    precip_type = None if precip_type_raw is None or np.ma.is_masked(precip_type_raw) else str(precip_type_raw)

    watch_type_raw = getattr(prof, "watch_type", None)
    watch_type_name = None if watch_type_raw is None or np.ma.is_masked(watch_type_raw) else str(watch_type_raw)

    def sars_payload(matches: Any, kind: str) -> dict[str, Any]:
        try:
            quality_ids = []
            for item in list(matches[0])[:10]:
                if isinstance(item, bytes):
                    quality_ids.append(item.decode("utf-8", errors="replace"))
                else:
                    quality_ids.append(str(item))
            quality_values = []
            for value in list(matches[1])[:10]:
                if isinstance(value, bytes):
                    quality_values.append(value.decode("utf-8", errors="replace"))
                else:
                    num = _safe_float(value)
                    quality_values.append(round(num, 2) if num is not None else str(value))
            loose = int(float(matches[2])) if len(matches) > 2 else 0
            severe_count = int(float(matches[3])) if len(matches) > 3 else 0
            probability = _safe_float(matches[4]) if len(matches) > 4 else None
            return {
                "kind": kind,
                "quality_ids": quality_ids,
                "quality_values": quality_values,
                "quality_count": len(quality_ids),
                "loose_count": loose,
                "severe_count": severe_count,
                "probability": probability,
            }
        except Exception:
            return {"kind": kind, "quality_ids": [], "quality_values": [], "quality_count": 0, "loose_count": 0, "severe_count": 0, "probability": None}

    hail_sars = sars_payload(getattr(prof, "matches", ([], [], 0, 0, 0)), "hail")
    supercell_sars = sars_payload(getattr(prof, "supercell_matches", ([], [], 0, 0, 0)), "supercell")

    def f_to_c(value: float | None) -> float | None:
        return (value - 32.0) * (5.0 / 9.0) if value is not None else None

    def r(value: Any, digits: int = 1) -> float | None:
        number = _safe_float(value)
        return round(number, digits) if number is not None else None

    def round_vector(vector: dict[str, float | None]) -> dict[str, float | None]:
        return {key: r(value, 0 if key == "direction" else 1) for key, value in vector.items()}

    profile_json = {
        "pressure": [r(p["pressure"], 1) for p in points],
        "height": [r(p["height"], 0) for p in points],
        "height_agl": [r(max(0.0, p["height"] - surface_height), 0) for p in points],
        "temperature": [r(p["temperature"], 1) for p in points],
        "dewpoint": [r(p["dewpoint"], 1) for p in points],
        "u": [r(p["u"], 1) for p in points],
        "v": [r(p["v"], 1) for p in points],
        "wind_speed": [r(math.hypot(p["u"], p["v"]), 1) for p in points],
        "wind_direction": [r(wind_direction_deg(p["u"], p["v"]), 0) for p in points],
        "omega": [r(p.get("omega"), 3) for p in points],
    }

    # Arredonda os índices depois do cálculo, preservando null quando mascarados.
    for parcel in (sb, ml, fcst, mu, eff):
        for key in ("cape", "cin", "cape_3km", "cape_6km", "cape_to_freezing", "lcl", "lfc", "el", "lcl_pressure", "lfc_pressure", "el_pressure", "freezing_height", "minus10_height", "minus20_height", "minus30_height", "li5", "li3", "brn", "brn_shear", "brn_u", "brn_v", "cap_strength", "source_pressure", "source_temperature", "source_dewpoint"):
            parcel[key] = r(parcel.get(key), 1 if key in {"li5", "li3"} else 0)


    valid_dt = run_dt + dt.timedelta(hours=fh)
    return {
        "model": "ECMWF IFS 0.25°",
        "model_id": "ecmwf_ifs025_openmeteo",
        "latitude": lat,
        "longitude": lon,
        "grid_latitude": r(grid_lat, 3),
        "grid_longitude": r(grid_lon, 3),
        "run": run_dt.isoformat().replace("+00:00", "Z"),
        "run_cycle": f"{run_dt.hour:02d}Z",
        "forecast_hour": fh,
        "valid": valid_dt.isoformat().replace("+00:00", "Z"),
        "surface_elevation_m": r(surface_height, 0),
        "surface_pressure_hpa": r(surface_pressure, 1),
        "profile": profile_json,
        "parcels": {"sb": sb, "ml": ml, "fcst": fcst, "mu": mu, "eff": eff},
        "thermodynamics": {
            "sbcape": sb["cape"], "sbcin": sb["cin"],
            "mlcape": ml["cape"], "mlcin": ml["cin"],
            "fcstcape": fcst["cape"], "fcstcin": fcst["cin"],
            "mucape": mu["cape"], "mucin": mu["cin"],
            "sb_cape_3km": sb["cape_3km"], "ml_cape_3km": ml["cape_3km"], "mu_cape_3km": mu["cape_3km"],
            "sb_cape_6km": sb["cape_6km"], "ml_cape_6km": ml["cape_6km"], "mu_cape_6km": mu["cape_6km"],
            "mu_cape_to_freezing": mu["cape_to_freezing"],
            "lcl": ml["lcl"], "lfc": ml["lfc"], "el": ml["el"],
            "lifted_index": sb["li5"], "lifted_index_300": sb["li3"],
            "pwat": r(pwat_in * 25.4 if pwat_in is not None else None, 1),
            "pwat_in": r(pwat_in, 2),
            "k_index": r(k_index, 1), "totals_totals": r(totals_totals, 1),
            "lapse_0_3km": r(lapse_03, 1), "lapse_3_6km": r(lapse_36, 1),
            "lapse_850_500": r(lapse_850_500, 1), "lapse_700_500": r(lapse_700_500, 1),
            "max_lapse_2_6km": r(max_lapse_26, 1),
            "convective_temperature_c": r(f_to_c(conv_temp_f), 1), "convective_temperature_f": r(conv_temp_f, 0),
            "max_temperature_c": r(f_to_c(max_temp_f), 1), "max_temperature_f": r(max_temp_f, 0),
            "mean_mixratio": r(mean_mixr, 1), "low_level_rh": r(low_rh, 0), "mid_level_rh": r(mid_rh, 0),
            "dcape": r(dcape, 0), "downrush_temperature_c": r(f_to_c(drush_f), 1), "downrush_temperature_f": r(drush_f, 0),
            "brn": r(mu.get("brn"), 1), "brn_shear": r(mu.get("brn_shear"), 0),
        },
        "kinematics": {
            "shear_01km": r(shear01, 1), "shear_03km": r(shear03, 1), "shear_06km": r(shear06, 1),
            "shear_08km": r(shear08, 1), "shear_09km": r(shear09, 1),
            "lcl_el_shear": r(lcl_el_shear, 1), "effective_layer_shear": r(eff_shear, 1), "effective_bulk_wind": r(ebwd if ebwd is not None else ebwspd, 1),
            "srh_01km": r(srh01, 0), "srh_03km": r(srh03, 0), "effective_srh": r(effective_srh, 0),
            "effective_inflow_bottom_m": r(effective_bottom, 0), "effective_inflow_top_m": r(effective_top, 0),
            "critical_angle": r(critical_angle, 0),
            "mean_wind_01km": round_vector(mean01), "mean_wind_03km": round_vector(mean03), "mean_wind_06km": round_vector(mean06), "mean_wind_08km": round_vector(mean08),
            "mean_wind_lcl_el": round_vector(mean_lcl_el), "mean_wind_effective": round_vector(mean_eff), "mean_wind_ebw": round_vector(mean_ebw),
            "srw_01km_vector": round_vector(srw01), "srw_03km_vector": round_vector(srw03), "srw_06km_vector": round_vector(srw06), "srw_08km_vector": round_vector(srw08),
            "srw_4_5km_vector": round_vector(srw45), "srw_lcl_el_vector": round_vector(srw_lcl_el), "srw_effective": round_vector(srw_eff), "srw_ebw": round_vector(srw_ebw),
            "srw_01km": r(srw01.get("speed"), 1), "srw_03km": r(srw03.get("speed"), 1), "srw_06km": r(srw06.get("speed"), 1), "srw_08km": r(srw08.get("speed"), 1),
            "srw_4_5km": r(srw45.get("speed"), 1), "srw_lcl_el": r(srw_lcl_el.get("speed"), 1),
            "wind_1km": round_vector(wind1km), "wind_6km": round_vector(wind6km),
            "mean_wind_06km_direction": r(mean06.get("direction"), 0), "mean_wind_06km_speed": r(mean06.get("speed"), 1),
            "storm_motion_u": r(storm_u, 1), "storm_motion_v": r(storm_v, 1),
            "storm_motion_speed": r(storm_speed, 1), "storm_motion_direction": r(storm_dir, 0),
            "storm_motion_name": selected_motion_name,
            "bunkers_rm": round_vector(bunkers_rm), "bunkers_lm": round_vector(bunkers_lm),
            "corfidi_up": round_vector(corfidi_up), "corfidi_down": round_vector(corfidi_down),
        },
        "severe": {
            "stp": r(stp, 2), "stp_fixed": r(stp_fixed, 2), "scp": r(scp, 2),
            "ehi_01km": r(ehi01, 2), "ehi_03km": r(ehi03, 2),
            "ship": r(ship, 2), "sherb": r(sherb, 2), "tei": r(tei, 2), "esp": r(esp, 2),
            "mmp": r(mmp, 2), "wndg": r(wndg, 2), "sig_severe": r(sig_severe, 0),
            "microburst": r(mburst, 2), "watch_type": watch_type_name,
        },
        "winter": {
            "dgz_bottom_hpa": r(dgz_pbot, 1), "dgz_top_hpa": r(dgz_ptop, 1),
            "dgz_mean_rh": r(dgz_meanrh, 0), "dgz_pw_in": r(dgz_pw_in, 2), "dgz_pw_mm": r(dgz_pw_in * 25.4 if dgz_pw_in is not None else None, 1),
            "dgz_mean_mixratio": r(dgz_meanq, 2), "dgz_mean_omega": r(dgz_meanomega, 2), "oprh": r(oprh, 3),
            "initial_phase_pressure_hpa": r(initial_phase_pressure, 1), "initial_phase": initial_phase, "initial_phase_temp_c": r(initial_phase_temp, 1), "initial_phase_state": initial_state,
            "temperature_positive_energy": r(tpos, 1), "temperature_negative_energy": r(tneg, 1), "temperature_layer_top_hpa": r(ttop, 1), "temperature_layer_bottom_hpa": r(tbot, 1),
            "wetbulb_positive_energy": r(wpos, 1), "wetbulb_negative_energy": r(wneg, 1), "wetbulb_layer_top_hpa": r(wtop, 1), "wetbulb_layer_bottom_hpa": r(wbot, 1),
            "precip_type": precip_type,
        },
        "analogs": {
            "hail": hail_sars, "supercell": supercell_sars,
            "sars_hail_count": hail_sars["quality_count"], "sars_supercell_count": supercell_sars["quality_count"],
            "database_scope": "SARS/SHARPpy (base calibrada com casos dos EUA; usar apenas como analogia fora do CONUS)",
        },
        "source": "ECMWF IFS 0.25° via Open-Meteo + SHARPpy (stable-v2)",
        "attribution": "ECMWF / Open-Meteo",
        "cache": False,
    }



class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def guess_type(self, path: str) -> str:
        content_type = super().guess_type(path)
        lower_path = path.lower()
        if lower_path.endswith(".html") or lower_path.endswith(".css") or lower_path.endswith(".js"):
            media_type = content_type.split(";", 1)[0]
            return f"{media_type}; charset=utf-8"
        return content_type

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[LOG] {format % args}")

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        parsed_path = urlparse(self.path).path
        if parsed_path == "/api/ipmet/meta": self.handle_ipmet_meta(); return
        if parsed_path == "/api/ipmet/wms": self.handle_ipmet_wms(parse_qs(urlparse(self.path).query)); return
        if parsed_path == "/api/rainviewer/meta": self.handle_public_json(RAINVIEWER_META_URL, "RainViewer"); return
        if parsed_path == "/api/redemet/radar": self.handle_redemet_radar(parse_qs(urlparse(self.path).query)); return
        if parsed_path == "/api/redemet/stsc": self.handle_redemet_stsc(parse_qs(urlparse(self.path).query)); return
        if parsed_path == "/api/redemet/estacoes": self.handle_redemet_stations(parse_qs(urlparse(self.path).query)); return
        if parsed_path.startswith("/api/redemet/estacao/"):
            icao_code = unquote(parsed_path.rsplit("/", 1)[-1]).upper().strip()
            self.handle_redemet_station(icao_code); return
        if parsed_path.startswith("/api/rainviewer/tile/"): self.handle_rainviewer_tile(parsed_path); return
        if parsed_path == "/api/inea/frames": self.handle_inea_frames(parse_qs(urlparse(self.path).query)); return
        if parsed_path == "/api/inea/image": self.handle_inea_image(parse_qs(urlparse(self.path).query)); return
        if parsed_path == "/api/simepar/meta": self.handle_simepar_meta(); return
        if parsed_path == "/api/simepar/image": self.handle_simepar_image(parse_qs(urlparse(self.path).query)); return
        if parsed_path == "/api/xweather/lightning": self.handle_xweather_lightning(parse_qs(urlparse(self.path).query)); return
        if parsed_path == "/api/health": self.send_json(200, {"status": "ok", "service": "sideral", "domain": "sul4km"}); return
        if parsed_path == "/api/wrf/status":
            try:
                status_query = parse_qs(urlparse(self.path).query)
                model_key = str(status_query.get("model", ["gfs"])[0]).lower().strip()
                if model_key not in MODEL_REMOTE_BASES:
                    self.send_json(400, {"status": "error", "error": "Modelo invalido. Use gfs, icon ou ecmwf."})
                    return
                metadata = _wrf_remote_metadata(model_key)
                capabilities = metadata.get("capabilities")
                if not isinstance(capabilities, dict):
                    capabilities = {
                        "reflectivity": model_key == "gfs",
                        "precipitation": True,
                        "wind": True,
                        "temperature": True,
                        "humidity": True,
                        "mucape": True,
                        "bulkShear": model_key == "gfs",
                        "vorticity850": model_key == "gfs",
                        "waterVapor": model_key in {"gfs", "ecmwf"},
                    }
                self.send_json(200, {
                    "status": "ok",
                    "source": "github-model-data",
                    "model": model_key,
                    "resolutionKm": metadata.get("resolutionKm"),
                    "runDate": metadata.get("runDate"),
                    "runCycle": metadata.get("runCycle"),
                    "initTime": metadata.get("initTime"),
                    "generatedAt": metadata.get("generatedAt"),
                    "forecastLocalDate": metadata.get("forecastLocalDate"),
                    "timezone": metadata.get("timezone"),
                    "scope": metadata.get("scope"),
                    "temporalResolutionMinutes": metadata.get("temporalResolutionMinutes"),
                    "frameCount": metadata.get("frameCount"),
                    "frames": metadata.get("frames"),
                    "capabilities": capabilities,
                    "note": metadata.get("note"),
                })
            except Exception as exc:
                self.send_json(502, {"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            return
        if parsed_path == "/api/inmet/estacoes": self.handle_inmet_stations(); return
        if parsed_path.startswith("/api/inmet/observacao/"):
            station_code = unquote(parsed_path.rsplit("/", 1)[-1]).upper().strip()
            self.handle_inmet_observation(station_code); return
        if parsed_path == "/api/sinotica/meta": self.handle_synoptic_meta(parse_qs(urlparse(self.path).query)); return
        if parsed_path == "/api/sinotica/chart.png": self.handle_synoptic_png(); return
        if parsed_path == "/api/sinotica/sideral.svg": self.handle_synoptic_svg(); return

        # --- NOVO ENDPOINT SKEW-T ---
        if parsed_path == "/api/sounding":
            self.handle_sounding(parse_qs(urlparse(self.path).query)); return

        if parsed_path == "/mapa_estacoes_inmet_com_dados.html":
            self.path = "/mapa_estacoes_inmet_corrigido.html"
        super().do_GET()

    def handle_sounding(self, query: dict[str, list[str]]) -> None:
        """Sondagem IFS 0.25° via Open-Meteo, processada pelo núcleo do SHARPpy."""
        try:
            lat = float(query.get("lat", ["-25.43"])[0])
            lon = float(query.get("lon", ["-49.27"])[0])
            run_requested = query.get("run", ["latest"])[0].lower().strip()
            fh = int(query.get("fh", ["0"])[0])

            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                self.send_json(400, {"error": "Latitude/longitude inválidas.", "code": "BAD_COORDINATES"}); return
            if run_requested not in {"latest", "00", "06", "12", "18"}:
                self.send_json(400, {"error": "Run inválida. Use latest, 00, 06, 12 ou 18.", "code": "BAD_RUN"}); return
            if fh < 0 or fh > 144 or fh % 3 != 0:
                self.send_json(400, {"error": "Use forecast hours de F000 a F144 em intervalos de 3 horas.", "code": "BAD_FORECAST_HOUR"}); return

            cache_key = (round(lat, 2), round(lon, 2), run_requested, fh, "ecmwf-ifs025-openmeteo-sharppy-v2-stable")
            cached = sounding_cache.get(cache_key)
            if cached and time.monotonic() - float(cached.get("saved_at", 0.0)) < SOUNDING_CACHE_SECONDS:
                payload = dict(cached["data"])
                payload["cache"] = True
                self.send_json(200, payload); return

            modules = _ecmwf_runtime_modules()

            payload = None
            last_error: Exception | None = None
            for run_dt in _ecmwf_run_candidates(run_requested):
                try:
                    payload = _ecmwf_build_sounding_payload(
                        lat, lon, run_dt, fh, modules
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    _log_sounding_error(f"falha na rodada {run_dt:%Y%m%d_%H} F{fh:03d}", exc)
                    if run_requested != "latest":
                        break

            if payload is None:
                if last_error is not None:
                    _log_sounding_error("nenhuma rodada utilizável", last_error)
                self.send_json(
                    502,
                    {
                        "error": "Não foi possível obter o perfil ECMWF IFS 0.25° agora. Tente outra rodada ou novamente em alguns minutos.",
                        "code": "ECMWF_RETRIEVAL_FAILED",
                    },
                )
                return

            sounding_cache[cache_key] = {"saved_at": time.monotonic(), "data": payload}
            self.send_json(200, payload)

        except RuntimeError as exc:
            _log_sounding_error("erro de dependência/dados", exc)
            public_message = "O backend do Skew-T não conseguiu preparar os dados meteorológicos."
            if "Dependências do Skew-T" in str(exc):
                public_message = "O backend do Skew-T está com uma dependência indisponível."
            self.send_json(502, {"error": public_message, "code": "SOUNDING_BACKEND_ERROR"})
        except Exception as exc:
            _log_sounding_error("erro interno", exc)
            self.send_json(
                500,
                {
                    "error": "Erro interno ao processar o perfil meteorológico.",
                    "code": "SOUNDING_INTERNAL",
                },
            )

    def handle_ipmet_meta(self) -> None:
        try:
            response = requests.get(IPMET_RADAR_PAGE, headers=IPMET_HEADERS, timeout=18)
            response.raise_for_status()
            utc_match = re.search(r"data_hora\s*=\s*['\"](\d{8}_\d{6})", response.text)
            local_match = re.search(r"data_local\s*=\s*['\"]([^'\"]+)", response.text)
            if not utc_match: raise ValueError("IPMet não publicou o horário da última varredura")
            scan_utc = dt.datetime.strptime(utc_match.group(1), "%Y%m%d_%H%M%S").replace(tzinfo=dt.timezone.utc)
            age_minutes = max(0, (dt.datetime.now(dt.timezone.utc) - scan_utc).total_seconds() / 60)
            self.send_json(200, {"available": True, "provider": "IPMet/UNESP", "product": "PPI combinado (merged)", "scanTime": scan_utc.isoformat().replace("+00:00", "Z"), "localLabel": local_match.group(1) if local_match else None, "stale": age_minutes > 30, "ageMinutes": round(age_minutes, 1), "source": IPMET_RADAR_PAGE})
        except Exception as exc: self.send_json(502, {"available": False, "error": "Falha ao consultar o radar oficial do IPMet.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_ipmet_wms(self, query: dict[str, list[str]]) -> None:
        try:
            bbox_text = query.get("bbox", [""])[0]
            bbox = [float(value) for value in bbox_text.split(",")]
            if len(bbox) != 4 or any(not math.isfinite(value) or abs(value) > 20037509 for value in bbox): raise ValueError("bbox Web Mercator inválido")
            width = int(query.get("width", ["512"])[0])
            height = int(query.get("height", ["512"])[0])
            if width not in (256, 512, 1024) or height not in (256, 512, 1024): raise ValueError("dimensão de tile não permitida")
            params = {"map": IPMET_MAP_FILE, "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "LAYERS": "merged", "STYLES": "", "FORMAT": "image/png", "TRANSPARENT": "true", "SRS": "EPSG:900913", "BBOX": ",".join(f"{value:.4f}" for value in bbox), "WIDTH": str(width), "HEIGHT": str(height)}
            response = requests.get(IPMET_WMS_URL, params=params, headers=IPMET_HEADERS, timeout=22)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "image/" not in content_type.lower() or len(response.content) < 100: raise ValueError("IPMet não retornou uma imagem WMS válida")
            body = response.content
            self.send_response(200)
            self.send_header("Content-Type", content_type.split(";", 1)[0])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=90, stale-if-error=600")
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, TypeError) as exc: self.send_json(400, {"error": str(exc)})
        except requests.RequestException as exc: self.send_json(502, {"error": "Radar IPMet temporariamente indisponível.", "details": str(exc)})

    def handle_rainviewer_tile(self, parsed_path: str) -> None:
        tile_path = parsed_path.removeprefix("/api/rainviewer/tile/")
        parts = tile_path.split("/")
        valid_id = len(parts) > 2 and bool(parts[2]) and all(char in "0123456789abcdefABCDEF" for char in parts[2])
        valid_numbers = len(parts) == 9 and all(parts[index].isdigit() for index in (3, 4, 5, 6))
        if len(parts) != 9 or parts[0] != "v2" or parts[1] != "radar" or not valid_id or not valid_numbers or parts[3] not in ("256", "512") or parts[7] != "2" or parts[8] != "1_1.png":
            self.send_json(400, {"error": "Tile RainViewer inválido."}); return
        upstream = f"https://tilecache.rainviewer.com/{tile_path}"
        try:
            response = requests.get(upstream, timeout=15)
            response.raise_for_status()
            body = response.content
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=120")
            self.end_headers()
            self.wfile.write(body)
        except requests.RequestException as exc: self.send_json(502, {"error": "Falha ao baixar tile do RainViewer.", "details": str(exc)})

    def handle_public_json(self, url: str, provider: str) -> None:
        try:
            response = requests.get(url, headers={"User-Agent": INMET_HEADERS["User-Agent"], "Accept": "application/json"}, timeout=20)
            response.raise_for_status()
            self.send_json(200, response.json())
        except Exception as exc: self.send_json(502, {"error": f"Falha ao consultar {provider}.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_inea_frames(self, query: dict[str, list[str]]) -> None:
        radar = query.get("radar", [""])[0].lower()
        product = query.get("product", ["zh"])[0].lower()
        if radar not in {"gua", "mac"} or product not in {"zh", "rr"}: self.send_json(400, {"error": "Radar ou produto INEA inválido."}); return
        try:
            response = requests.get(f"{INEA_RADAR_TOOL_URL}/frames.php", params={"type": "radar", "radar": radar, "product": product, "hours": 12, "max": 15}, headers={"User-Agent": INMET_HEADERS["User-Agent"], "Accept": "application/json"}, timeout=25, verify=False)
            response.raise_for_status()
            payload = response.json()
            images = payload.get("images") if isinstance(payload, dict) else None
            if not isinstance(images, list) or not images: raise ValueError("INEA não publicou quadros para este radar")
            safe_images = [str(image).rsplit("/", 1)[-1] for image in images if re.fullmatch(r"[A-Za-z0-9_.-]+\.png", str(image).rsplit("/", 1)[-1])]
            if not safe_images: raise ValueError("INEA retornou nomes de imagem inválidos")
            labels = payload.get("labels", [])
            self.send_json(200, {"radar": radar, "product": product, "images": safe_images, "labels": labels[-len(safe_images):], "step_min": payload.get("step_min")})
        except Exception as exc: self.send_json(502, {"error": "Falha ao consultar os quadros oficiais do INEA.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_inea_image(self, query: dict[str, list[str]]) -> None:
        radar = query.get("radar", [""])[0].lower()
        product = query.get("product", ["zh"])[0].lower()
        filename = query.get("file", [""])[0]
        if radar not in {"gua", "mac"} or product not in {"zh", "rr"} or not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", filename): self.send_json(400, {"error": "Imagem INEA inválida."}); return
        try:
            cache_key = f"{radar}/{product}/{filename}"
            cached = inea_radar_image_cache.get(cache_key)
            if cached is not None:
                self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(cached))); self.end_headers(); self.wfile.write(cached); return
            upstream = f"{INEA_RADAR_TOOL_URL}/img/img-radar-{radar}-{product}/{filename}"
            response = requests.get(upstream, headers={"User-Agent": INMET_HEADERS["User-Agent"]}, timeout=22, verify=False)
            response.raise_for_status()
            if "image/png" not in response.headers.get("Content-Type", "").lower() or len(response.content) < 300: raise ValueError("INEA não retornou PNG válido")
            body = png_black_to_transparent(response.content)
            inea_radar_image_cache[cache_key] = body
            while len(inea_radar_image_cache) > 64: inea_radar_image_cache.pop(next(iter(inea_radar_image_cache)))
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=300")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc: self.send_json(502, {"error": "Imagem do radar INEA indisponível.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_simepar_meta(self) -> None:
        frames, errors = [], []
        for frame_number in range(8, 0, -1):
            try:
                response = requests.get(f"{SIMEPAR_RADAR_URL}/product{frame_number}.jpeg", headers={"User-Agent": INMET_HEADERS["User-Agent"]}, stream=True, timeout=12)
                response.raise_for_status()
                modified = response.headers.get("Last-Modified")
                date = parsedate_to_datetime(modified).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z") if modified else None
                frames.append({"frame": frame_number, "date": date})
                response.close()
            except Exception as exc: errors.append(f"product{frame_number}: {type(exc).__name__}: {exc}"); continue
        if not frames: self.send_json(502, {"error": "SIMEPAR não publicou imagens acessíveis.", "details": errors[:2]}); return
        self.send_json(200, {"frames": frames, "provider": "SIMEPAR", "note": "Mosaico oficial; Teixeira Soares está temporariamente desativado e a publicação atual é baseada em Cascavel."})

    def handle_simepar_image(self, query: dict[str, list[str]]) -> None:
        try:
            frame_number = int(query.get("frame", ["1"])[0])
            if frame_number not in range(1, 9): raise ValueError("Quadro SIMEPAR inválido")
            response = requests.get(f"{SIMEPAR_RADAR_URL}/product{frame_number}.jpeg", timeout=22)
            response.raise_for_status()
            if "image/jpeg" not in response.headers.get("Content-Type", "").lower() or len(response.content) < 1000: raise ValueError("SIMEPAR não retornou JPEG válido")
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(response.content)))
            self.send_header("Cache-Control", "public, max-age=180")
            self.end_headers()
            self.wfile.write(response.content)
        except ValueError as exc: self.send_json(400, {"error": str(exc)})
        except Exception as exc: self.send_json(502, {"error": "Imagem do SIMEPAR indisponível.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_redemet_radar(self, query: dict[str, list[str]]) -> None:
        product = query.get("product", ["03km"])[0].lower()
        if product not in REDEMET_PRODUCTS: self.send_json(400, {"error": "Produto REDEMET inválido."}); return
        try: anima = min(15, max(1, int(query.get("anima", ["10"])[0])))
        except ValueError: self.send_json(400, {"error": "Quantidade de quadros inválida."}); return
        self.handle_public_json(f"{REDEMET_API_URL}/produtos/radar/{product}?anima={anima}&api_key={REDEMET_API_KEY}", "REDEMET")

    def handle_redemet_stsc(self, query: dict[str, list[str]]) -> None:
        try: anima = min(6, max(1, int(query.get("anima", ["3"])[0])))
        except ValueError: self.send_json(400, {"error": "Quantidade de quadros inválida."}); return
        self.handle_public_json(f"{REDEMET_API_URL}/produtos/stsc?anima={anima}&api_key={REDEMET_API_KEY}", "raios STSC/REDEMET")

    def fetch_redemet_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = requests.get(f"{REDEMET_API_URL}{path}", params=params, headers={"X-Api-Key": REDEMET_API_KEY, "User-Agent": INMET_HEADERS["User-Agent"], "Accept": "application/json"}, timeout=25)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") is not True: raise ValueError("A REDEMET retornou uma resposta sem dados válidos.")
        return payload

    @staticmethod
    def normalize_redemet_status(value: Any) -> str:
        status = str(value or "cinza").strip().lower()
        if status in {"g", "green", "verde"}: return "verde"
        if status in {"y", "yellow", "amarelo"}: return "amarelo"
        if status in {"r", "red", "vermelho"}: return "vermelho"
        return "cinza"

    def redemet_station_catalog(self) -> list[dict[str, Any]]:
        now_monotonic = time.monotonic()
        cached = redemet_station_catalog_cache.get("data")
        if cached is not None and now_monotonic - float(redemet_station_catalog_cache.get("saved_at", 0.0)) < REDEMET_STATION_CACHE_SECONDS: return cached
        payload = self.fetch_redemet_json("/aerodromos/status/pais/BRASIL")
        aerodromes_payload = self.fetch_redemet_json("/aerodromos/", {"pais": "BRASIL"})
        rows = payload.get("data")
        if not isinstance(rows, list): raise ValueError("Catálogo de aeródromos em formato inesperado.")
        details = {str(item.get("cod") or "").upper(): item for item in aerodromes_payload.get("data", []) if isinstance(item, dict) and item.get("cod")}
        stations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, list) or len(row) < 5: continue
            icao = str(row[0] or "").upper().strip()
            latitude = inmet_safe_float(row[2])
            longitude = inmet_safe_float(row[3])
            if not REDEMET_ICAO_RE.fullmatch(icao) or latitude is None or longitude is None or icao in seen: continue
            seen.add(icao)
            detail = details.get(icao, {})
            city = str(detail.get("cidade") or "").strip() or None
            city_match = re.search(r"/([A-Z]{2})$", city or "", re.IGNORECASE)
            stations.append({"icao": icao, "nome": str(detail.get("nome") or row[1] or icao).strip(), "cidade": city, "latitude": latitude, "longitude": longitude, "altitudeMetros": inmet_safe_float(detail.get("altitude_metros")), "status": self.normalize_redemet_status(row[4]), "uf": city_match.group(1).upper() if city_match else None})
        stations.sort(key=lambda station: (station["nome"].casefold(), station["icao"]))
        redemet_station_catalog_cache["saved_at"] = now_monotonic
        redemet_station_catalog_cache["data"] = stations
        return stations

    def handle_redemet_stations(self, query: dict[str, list[str]] | None = None) -> None:
        try:
            if (query or {}).get("refresh", ["0"])[0].lower() in {"1", "true", "yes"}:
                redemet_station_catalog_cache["saved_at"] = 0.0; redemet_station_catalog_cache["data"] = None
            stations = self.redemet_station_catalog()
            counts = {status: sum(station["status"] == status for station in stations) for status in ("verde", "amarelo", "vermelho", "cinza")}
            self.send_json(200, {"provider": "REDEMET / DECEA", "source": "aerodromos/status/pais/BRASIL", "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"), "count": len(stations), "counts": counts, "stations": stations})
        except Exception as exc: self.send_json(502, {"error": "Falha ao consultar as estações da REDEMET.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_redemet_station(self, icao_code: str) -> None:
        if not REDEMET_ICAO_RE.fullmatch(icao_code): self.send_json(400, {"error": "Código ICAO inválido."}); return
        now_monotonic = time.monotonic()
        cached = redemet_station_observation_cache.get(icao_code)
        if cached and now_monotonic - float(cached["saved_at"]) < 60:
            data = dict(cached["data"]); data["cache"] = True; self.send_json(200, data); return
        try:
            payload = self.fetch_redemet_json("/aerodromos/info", {"localidade": icao_code, "metar": "sim", "taf": "sim"})
            raw = payload.get("data")
            if not isinstance(raw, dict): raise ValueError("Observação aeronáutica em formato inesperado.")
            station = next((item for item in self.redemet_station_catalog() if item["icao"] == icao_code), None)
            data = {"provider": "REDEMET / DECEA", "officialUrl": f"https://redemet.decea.mil.br/?i=facilidades&p=consulta-mensagem&localidade={icao_code}", "cache": False, "icao": icao_code, "nome": raw.get("nome") or (station or {}).get("nome") or icao_code, "cidade": raw.get("cidade"), "latitude": (station or {}).get("latitude"), "longitude": (station or {}).get("longitude"), "status": (station or {}).get("status", "cinza"), "observedAt": raw.get("data") or raw.get("data_hora"), "temperatura": raw.get("temperatura"), "umidade": raw.get("ur"), "visibilidade": raw.get("visibilidade"), "teto": raw.get("teto"), "ceu": raw.get("ceu"), "tempo": raw.get("condicoes_tempo"), "vento": raw.get("vento"), "metar": raw.get("metar"), "taf": raw.get("taf")}
            redemet_station_observation_cache[icao_code] = {"saved_at": now_monotonic, "data": data}
            while len(redemet_station_observation_cache) > 200: redemet_station_observation_cache.pop(next(iter(redemet_station_observation_cache)))
            self.send_json(200, data)
        except Exception as exc: self.send_json(502, {"error": f"Falha ao consultar {icao_code} na REDEMET.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_xweather_lightning(self, query: dict[str, list[str]]) -> None:
        client_id = os.getenv("XWEATHER_CLIENT_ID", "").strip()
        client_secret = os.getenv("XWEATHER_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret: self.send_json(503, {"error": "Configure XWEATHER_CLIENT_ID e XWEATHER_CLIENT_SECRET."}); return
        try:
            south, west = float(query.get("south", ["-34"])[0]), float(query.get("west", ["-74"])[0])
            north, east = float(query.get("north", ["6"])[0]), float(query.get("east", ["-34"])[0])
            lat_step, lon_step = 4.0, 5.0
            points: dict[str, dict[str, Any]] = {}
            lat = south
            while lat <= north:
                lon = west
                while lon <= east:
                    url = "https://data.api.xweather.com/lightning/closest"
                    params = {"p": f"{lat:.2f},{lon:.2f}", "radius": "100km", "limit": 1000, "filter": "all", "client_id": client_id, "client_secret": client_secret}
                    response = requests.get(url, params=params, timeout=12)
                    response.raise_for_status()
                    for item in response.json().get("response", []):
                        loc = item.get("loc", {})
                        if "lat" in loc and "long" in loc:
                            key = f"{float(loc['lat']):.3f},{float(loc['long']):.3f}"
                            points[key] = {"lat": loc["lat"], "lon": loc["long"], "type": item.get("ob", {}).get("pulse", {}).get("type"), "age": item.get("ob", {}).get("age")}
                    lon += lon_step
                lat += lat_step
            self.send_json(200, {"strikes": list(points.values()), "queries": len(points)})
        except Exception as exc: self.send_json(502, {"error": "Falha ao consultar raios Xweather.", "details": str(exc)})

    def handle_synoptic_meta(self, query: dict[str, list[str]] | None = None) -> None:
        try:
            refresh = (query or {}).get("refresh", ["0"])[0].lower() in {"1", "true", "yes"}
            metadata = ensure_synoptic_chart(force=refresh)
            self.send_json(200, metadata)
        except Exception as exc: self.send_json(502, {"error": "Falha ao gerar a carta sinótica.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_synoptic_png(self) -> None:
        try:
            ensure_synoptic_chart()
            body = SYNOPTIC_PNG_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=900")
            self.send_header("Content-Disposition", 'inline; filename="carta-sinotica-inmet.png"')
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc: self.send_json(502, {"error": "Falha ao carregar a carta sinótica oficial.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_synoptic_svg(self) -> None:
        self.send_response(302)
        self.send_header("Location", "/api/sinotica/chart.png")
        self.end_headers()

    def handle_inmet_stations(self) -> None:
        try:
            data = fetch_inmet_json(INMET_STATIONS_URL, timeout=25)
            if not isinstance(data, list): self.send_json(502, {"error": "Catálogo INMET em formato inesperado."}); return
            self.send_json(200, data)
        except Exception as exc: self.send_json(502, {"error": "Falha ao consultar o catálogo do INMET.", "details": f"{type(exc).__name__}: {exc}"})

    def handle_inmet_observation(self, station_code: str) -> None:
        if not INMET_STATION_CODE_RE.fullmatch(station_code): self.send_json(400, {"error": "Código de estação inválido."}); return
        cached = inmet_observation_cache.get(station_code)
        now_monotonic = time.monotonic()
        if cached and now_monotonic - cached["saved_at"] < INMET_CACHE_SECONDS:
            response = dict(cached["data"]); response["cache"] = True; self.send_json(200, response); return
        try:
            data = get_latest_inmet_observation(station_code)
            data["cache"] = False
            inmet_observation_cache[station_code] = {"saved_at": now_monotonic, "data": data}
            self.send_json(200, data)
        except requests.exceptions.Timeout as exc: self.send_json(504, {"error": "O INMET demorou demais para responder.", "details": str(exc)})
        except requests.exceptions.RequestException as exc: self.send_json(502, {"error": "Falha ao consultar observação do INMET.", "details": str(exc)})
        except Exception as exc: self.send_json(502, {"error": "Erro ao interpretar observação do INMET.", "details": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:
        path = self.path.lower().strip()
        if path not in {"/api/gfs/cells", "/api/wrf/cells", "/api/ecmwf/cells", "/api/meteoblue/cells"}:
            self.send_json(404, {"error": f"Rota invalida: {self.path}"}); return
        try:
            payload = self.read_json_body()
            bounds = self.parse_bounds(payload)
            hours = max(0, int(payload.get("hours", 0)))
            grid_x = max(1, min(260, int(payload.get("gridX", 64))))
            grid_y = max(1, min(240, int(payload.get("gridY", 63))))
            wrf_model = str(payload.get("wrfModel", "gfs")).lower()
            if wrf_model not in WRF_MODEL_OUTPUTS: wrf_model = "icon"
            response_extra: dict[str, Any] = {}
            if path == "/api/gfs/cells":
                try:
                    cells = build_gfs_cells(bounds, hours, grid_x, grid_y)
                    response_extra = {"gridX": grid_x, "gridY": grid_y, "source": "GFS_REFLECTIVITY", "model": "gfs_direct"}
                except Exception as exc:
                    wrf_payload = build_wrf_cells(bounds, hours, grid_x, grid_y, "gfs")
                    cells = wrf_payload["cells"]
                    response_extra = {key: value for key, value in wrf_payload.items() if key != "cells"}
            elif path == "/api/wrf/cells":
                # Todos os modelos publicados pelo GitHub usam a mesma interface.
                # GFS = WRF 4 km com REFL_10CM. ICON/ECMWF = campos oficiais diretos.
                wrf_payload = build_remote_wrf_cells(bounds, hours, grid_x, grid_y, wrf_model)
                cells = wrf_payload["cells"]
                response_extra = {key: value for key, value in wrf_payload.items() if key != "cells"}
            elif path == "/api/meteoblue/cells": cells = build_meteoblue_cells(bounds, hours, grid_x, grid_y)
            else:
                wrf_payload = build_wrf_cells(bounds, hours, grid_x, grid_y, "ecmwf")
                cells = wrf_payload["cells"]
                response_extra = {key: value for key, value in wrf_payload.items() if key != "cells"}
            self.send_json(200, {"cells": cells, "hours": hours, "stepUsed": hours, **response_extra})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc: self.send_json(400, {"error": f"Payload invalido: {exc}"})
        except ExternalAPIError as exc: self.send_json(502, {"error": str(exc)})
        except WRFDomainError as exc: self.send_json(409, {"error": str(exc)})
        except FileNotFoundError as exc: self.send_json(409, {"error": str(exc)})
        except Exception as exc: self.send_json(500, {"error": str(exc)})

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0: raise ValueError("corpo vazio")
        body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(body)
        if not isinstance(payload, dict): raise ValueError("JSON precisa ser um objeto")
        return payload

    def parse_bounds(self, payload: dict[str, Any]) -> dict[str, float]:
        bounds = {key: float(payload[key]) for key in ("south", "west", "north", "east")}
        if bounds["south"] >= bounds["north"] or bounds["west"] >= bounds["east"]: raise ValueError("bounds invalidos")
        return bounds

    def send_json(self, status_code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

def main() -> None:
    threading.Thread(target=render_keep_alive, daemon=True, name='render-keep-alive').start()
    server = ThreadingHTTPServer((DEFAULT_HOST, DEFAULT_PORT), Handler)
    print("\n" + "=" * 80)
    print("SERVIDOR SIDERAL RODANDO")
    print(f"  URL base: http://{DEFAULT_HOST}:{DEFAULT_PORT}/")
    print("=" * 80 + "\n")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nServidor parado.")
    finally: server.server_close()

if __name__ == "__main__":
    main()
