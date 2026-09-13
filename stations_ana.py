from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

import server_legacy as legacy
from stations_common import safe_float, station

ANA_TELEMETRY_URL = "https://telemetriaws1.ana.gov.br/ServiceANA.asmx/ListaEstacoesTelemetricas"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_map(element: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for child in list(element):
        key = _local(child.tag)
        value = (child.text or "").strip()
        if value:
            out[key] = value
    return out


def _pick(row: dict[str, str], *names: str) -> str | None:
    lowered = {key.casefold(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.casefold())
        if value not in (None, ""):
            return value
    return None


def _split_city_uf(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    text = re.sub(r"\s+", " ", value).strip()
    match = re.match(r"^(.*?)[\s/-]+([A-Z]{2})$", text, re.IGNORECASE)
    if match:
        city = match.group(1).strip(" -/") or None
        return city, match.group(2).upper()
    return text or None, None


def load() -> list[dict[str, Any]]:
    """Carrega o catálogo telemétrico público da ANA em uma única requisição.

    Este serviço é legado e pode desaparecer; por isso o agregador Sideral o
    mantém isolado, com cache e stale fallback. Não fazemos uma chamada por
    estação, evitando pressionar tanto a ANA quanto o Render.
    """
    response = legacy.requests.get(
        ANA_TELEMETRY_URL,
        params={"statusEstacoes": "", "origem": "0"},
        headers={
            "User-Agent": "SideralMeteorologia/1.0 (station-map)",
            "Accept": "application/xml,text/xml,*/*",
        },
        timeout=45,
    )
    response.raise_for_status()
    if not response.content:
        raise ValueError("ANA retornou resposta vazia")

    root = ET.fromstring(response.content)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for element in root.iter():
        raw = _text_map(element)
        if not raw:
            continue
        code = _pick(raw, "CodEstacao", "CodigoEstacao", "Codigo", "EstacaoCodigo")
        latitude = safe_float(_pick(raw, "Latitude", "Lat"))
        longitude = safe_float(_pick(raw, "Longitude", "Lon", "Long"))
        if not code or latitude is None or longitude is None:
            continue
        code = re.sub(r"\s+", "", str(code))
        if code in seen or not (-35.8 <= latitude <= 6.8 and -75.5 <= longitude <= -30.0):
            continue
        seen.add(code)

        municipality = _pick(raw, "Municipio-UF", "MunicipioUF", "Municipio", "MunicipioEstado")
        city, uf = _split_city_uf(municipality)
        explicit_uf = _pick(raw, "UF", "SiglaUF", "Estado")
        if explicit_uf and len(explicit_uf.strip()) == 2:
            uf = explicit_uf.strip().upper()

        status_raw = _pick(raw, "StatusEstacao", "Status")
        status_map = {"0": "ativo", "1": "manutencao"}
        status = status_map.get(str(status_raw).strip(), str(status_raw).strip() if status_raw else None)
        name = _pick(raw, "NomeEstacao", "Nome", "Estacao") or code
        river = _pick(raw, "NomeRio", "Rio")

        rows.append(
            station(
                network="ANA",
                code=code,
                name=name,
                latitude=latitude,
                longitude=longitude,
                uf=uf,
                city=city,
                altitude=safe_float(_pick(raw, "Altitude")),
                kind="hidrometeorologica",
                status=status,
                extra={
                    "basin": _pick(raw, "Bacia"),
                    "subbasin": _pick(raw, "SubBacia", "Sub-bacia"),
                    "river": river,
                    "riverCode": _pick(raw, "CodRio", "CodigoRio"),
                    "operator": _pick(raw, "Operadora", "Operador"),
                    "responsible": _pick(raw, "Responsavel", "Responsável"),
                    "origin": _pick(raw, "Origem"),
                    "legacyService": True,
                },
            )
        )

    if not rows:
        raise ValueError("ANA não retornou estações telemétricas com coordenadas válidas")
    rows.sort(key=lambda item: (item.get("uf") or "", item.get("city") or "", item.get("name") or "", item.get("code") or ""))
    return rows
