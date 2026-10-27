"""Bounded public-source adapters and reproducible geographic estimates.

No URLs supplied by a browser are fetched. Source failure is data, not a zero.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from io import BytesIO
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests
from pypdf import PdfReader
from shapely.geometry import Point, shape, box
from shapely.prepared import prep

DATA = Path(__file__).resolve().parents[1] / "data"
CENSUS = "https://www.statssa.gov.za/publications/Census2022inBrief/Census2022inBriefJune2024.pdf"
DEATHS = "https://www.statssa.gov.za/publications/P03093/P030932024.pdf"
POPULATION = "https://www.statssa.gov.za/publications/P0302/P03022024.pdf"
SARS = "https://www.sars.gov.za/wp-content/uploads/2025taxstats/2025-Tax-Statistics-PDF-.pdf"
MAIN = "https://services3.arcgis.com/GMycIhSIBQnnjV35/arcgis/rest/services/Census_2011_Main_Places_of_South_Africa/FeatureServer/1"
SUB = "https://services3.arcgis.com/GMycIhSIBQnnjV35/arcgis/rest/services/Census_2011_Sub_Places_of_South_Africa/FeatureServer/0"
HOSTS = {"www.statssa.gov.za", "www.sars.gov.za", "www.arcgis.com", "services7.arcgis.com", "services3.arcgis.com", "overpass-api.de", "overpass.kumi.systems", "overpass.private.coffee", "api.openstreetmap.org"}
_cache = {}
_cache_lock = threading.Lock()
_interactive_deadline = ContextVar('territory_source_deadline', default=None)


@contextmanager
def interactive_source_budget(seconds=6):
    """Share a short publisher budget across pagination and source fallbacks.

    Context-local state keeps interactive requests separate from durable research
    jobs. Socket reads are capped too, including the final read near the deadline.
    """
    token = _interactive_deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _interactive_deadline.reset(token)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def public_bytes(url, params=None, limit=32000000):
    """Known publishers only, checked redirects, bounded body/time and TTL cache."""
    key = (url, json.dumps(params, sort_keys=True))
    with _cache_lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 86400:
            return {**cached[1], "cached": True}
    deadline = time.monotonic() + 75
    interactive_deadline = _interactive_deadline.get()
    if interactive_deadline is not None:
        deadline = min(deadline, interactive_deadline)
    for redirect in range(4):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port:
            raise ValueError("Source host is not authorised")
        if parsed.hostname == "api.openstreetmap.org" and parsed.path != "/api/0.6/map.json":
            raise ValueError("Only the bounded public OSM map extract is authorised")
        if parsed.hostname == "services7.arcgis.com" and not parsed.path.startswith("/oeoyTUJC8HEeYsRB/arcgis/rest/services/"):
            raise ValueError("Only the official MDB organisation is authorised")
        if parsed.hostname == "www.arcgis.com" and not parsed.path.startswith("/sharing/rest/content/items/"):
            raise ValueError("Only fixed boundary publisher metadata is authorised")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout('Boundary publisher time budget exhausted')
        timeout = (8, 20) if interactive_deadline is None else (min(2, remaining / 2), min(3, remaining / 2))
        with requests.get(url, params=params, stream=True, timeout=timeout, allow_redirects=False,
                          headers={"User-Agent": "MartinsUnified/1.0 public territory research"}) as response:
            if response.is_redirect:
                url = urljoin(response.url, response.headers.get("Location", ""))
                params = None
                continue
            response.raise_for_status()
            pieces, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if time.monotonic() > deadline and interactive_deadline is not None:
                    raise requests.Timeout('Boundary publisher time budget exhausted')
                if size > limit or time.monotonic() > deadline:
                    raise ValueError("Source exceeded the response/time limit")
                pieces.append(chunk)
            body = b"".join(pieces)
            result = {"body": body, "retrieved_at": stamp(), "url": response.url,
                      "sha256": hashlib.sha256(body).hexdigest(), "cached": False}
            with _cache_lock:
                if len(_cache) >= 64:
                    _cache.pop(next(iter(_cache)))
                _cache[key] = (time.monotonic(), result)
            return result
    raise ValueError("Too many source redirects")


def public_json(url, params=None):
    downloaded=public_bytes(url,params,limit=12000000)
    value=json.loads(downloaded['body'])
    if isinstance(value,dict):
        value['_source_meta']={key:downloaded[key] for key in ['url','retrieved_at','sha256','cached']}
    return value


def mapped_data(query, bounds):
    """Use Overpass, with one cached, small-area OSM extract as fallback."""
    failures=[]
    for endpoint in ['https://overpass-api.de/api/interpreter','https://overpass.private.coffee/api/interpreter']:
        try:
            data=public_json(endpoint,{'data':query})
            if data.get('remark') or not isinstance(data.get('elements'),list):
                raise ValueError('Map query was incomplete')
            return {**data,'source_url':endpoint,'extract_fallback':False}
        except Exception as exc:
            failures.append(source_error(exc))
    west,south,east,north=bounds
    if (east-west)*(north-south) > .01:
        raise ValueError('Overpass unavailable; selected area exceeds the small-area fallback limit')
    endpoint='https://api.openstreetmap.org/api/0.6/map.json'
    data=public_json(endpoint,{'bbox':','.join(str(v) for v in bounds)})
    if not isinstance(data.get('elements'),list):raise ValueError('Invalid OSM map extract')
    rows=data['elements']
    nodes={item['id']:item for item in rows if item.get('type')=='node'}
    selected=[]
    for item in rows:
        tags=item.get('tags',{})
        road=tags.get('highway') in {'motorway','trunk','primary','secondary'}
        funeral=tags.get('shop')=='funeral_directors' or tags.get('amenity') in {'funeral_hall','crematorium'}
        if not road and not funeral:continue
        item=dict(item)
        if item.get('type')=='way':
            refs=item.get('nodes',[])
            if any(ref not in nodes for ref in refs):
                raise ValueError('OSM way geometry was incomplete')
            geometry=[{'lon':nodes[ref]['lon'],'lat':nodes[ref]['lat']} for ref in refs]
            item['geometry']=geometry
            if funeral and len(geometry)>=3:
                from shapely.geometry import Polygon
                points=[(p['lon'],p['lat']) for p in geometry]
                building=Polygon(points)
                if building.is_valid and building.area>0:
                    center=building.representative_point();item['center']={'lon':center.x,'lat':center.y}
        selected.append(item)
    return {'elements':selected,'source_url':endpoint+'?bbox='+','.join(str(v) for v in bounds),
        'extract_fallback':True,'overpass_failures':failures,'_source_meta':data.get('_source_meta',{})}


def pdf_text(url):
    downloaded = public_bytes(url)
    if not downloaded["body"].startswith(b"%PDF"):
        raise ValueError("Publisher did not return a PDF")
    reader = PdfReader(BytesIO(downloaded["body"]))
    if len(reader.pages) > 400:
        raise ValueError("Publication exceeds supported page count")
    pages = [p.extract_text() or "" for p in reader.pages]
    return pages, {key: value for key, value in downloaded.items() if key != "body"}


def grouped_row(text, label, columns):
    found = []
    for match in re.finditer(r"^\s*" + re.escape(label) + r"[ \t]+([^\n]+)", text, re.M):
        cells = re.split(r"\s{2,}", match.group(1).strip())
        if len(cells) == columns and all(re.fullmatch(r"[\d ,]+", c) for c in cells):
            found.append([int(c.replace(" ", "").replace(",", "")) for c in cells])
    if not found or any(row != found[0] for row in found):
        raise ValueError("Source table changed or the geographic row is ambiguous")
    return found[0]


def usual_residence_deaths(text, province):
    """Stats SA 2024 table 3.6: count, %, usual-residence count, %."""
    if 'Table 3.6' not in text or 'usual residence' not in text.lower():
        raise ValueError("Mortality table 3.6 or residence headings were not recognised")
    label = 'Kwa-Zulu Natal' if province == 'KwaZulu-Natal' else province
    count = r'(?:[0-9]{1,3}(?:[ \t]+[0-9]{3})+|[0-9]+)'
    percentage = r'[0-9]{1,3}[,.][0-9]+'
    pattern = (r'^\s*'+re.escape(label)+r'[ \t]+('+count+r')[ \t]+('+percentage+
               r')[ \t]+('+count+r')[ \t]+('+percentage+r')[ \t]*$')
    rows=[]
    for found in re.finditer(pattern,text,re.M):
        occurrence=int(re.sub(r'\s','',found[1])); residence=int(re.sub(r'\s','',found[3]))
        if not 0 < occurrence < 1000000 or not 0 < residence < 1000000 or any(not 0 <= float(found[c].replace(',','.')) <= 100 for c in [2,4]):
            raise ValueError("Mortality row failed range checks")
        rows.append(residence)
    if not rows or any(value != rows[0] for value in rows):
        raise ValueError("Mortality residence row was missing or ambiguous")
    return rows[0]


def provincial_population(text, province):
    if 'Appendix 1' not in text or '2024' not in text:
        raise ValueError('The 2024 provincial population appendix was not recognised')
    count=r'(?:[0-9]{1,3}(?:[ \t]+[0-9]{3})+|[0-9]+)'
    pattern=r'^\s*'+re.escape(province)+r'[ \t]+('+count+r')[ \t]+[0-9]{1,3}[,.][0-9]+[ \t]*$'
    values=[int(re.sub(r'\s','',m[1])) for m in re.finditer(pattern,text,re.M)]
    if not values or any(n!=values[0] for n in values) or not 100000 < values[0] < 30000000:
        raise ValueError('The provincial population row was missing or ambiguous')
    return values[0]


def sars_income(text, municipality_code):
    if not re.fullmatch(r"(?:[A-Z]{2,3}\d{0,3})", municipality_code):
        raise ValueError("A verified municipality code is required for SARS data")
    # 2023 and 2024: assessed taxpayers, taxable income (R million), assessed
    # tax (R million), average taxable income (Rand). Reject percentage rows.
    rows = []
    for line in text.splitlines():
        if re.search(r"\b" + re.escape(municipality_code) + r"\b", line) and "%" not in line:
            remainder = re.split(r"\b" + re.escape(municipality_code) + r"\b", line)[-1]
            cells = re.split(r"\s{2,}", remainder.strip())
            if len(cells) == 8 and all(re.fullmatch(r"[\d ,]+", c) for c in cells):
                rows.append([int(c.replace(" ", "").replace(",", "")) for c in cells])
    if not rows or any(r != rows[0] for r in rows) or not 1000 < rows[0][-1] < 10000000:
        raise ValueError("No unambiguous SARS municipality income row was found")
    return {"assessed_taxpayers": rows[0][4], "annual_average_taxable_income": rows[0][7]}


@lru_cache(maxsize=128)
def _tile(root, name, tile_size):
    path = Path(root) / (name + ".bin.gz")
    with gzip.open(path, "rb") as stream:
        data = stream.read(tile_size * tile_size * 4 + 1)
    if len(data) != tile_size * tile_size * 4:
        raise ValueError("Population tile is incomplete")
    values = struct.unpack("<" + "f" * (tile_size * tile_size), data)
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Population tile contains invalid counts")
    return values


def population_in_geometry(geometry, root=None):
    root = Path(root or DATA / "territory_population")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf8"))
    if manifest.get("encoding") != "gzip little-endian float32 population counts" or manifest.get("year") != 2026:
        raise ValueError("Unsupported population dataset manifest")
    west, north = manifest["origin"]
    step, size = manifest["step"], manifest["tileSize"]
    if not (0 < step <= .01 and 1 <= size <= 256):
        raise ValueError("Invalid population grid dimensions")
    polygon = shape(geometry)
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError("Select a valid, non-empty territory boundary")
    extent = box(west, north-manifest["height"]*step, west+manifest["width"]*step, north)
    if not extent.buffer(1e-10).covers(polygon):
        raise ValueError("The whole selected boundary must be within the population dataset coverage; no partial territory total was calculated")
    bounds = polygon.bounds
    x0 = max(0, math.floor((bounds[0] - west) / step))
    x1 = min(manifest["width"] - 1, math.ceil((bounds[2] - west) / step))
    y0 = max(0, math.floor((north - bounds[3]) / step))
    y1 = min(manifest["height"] - 1, math.ceil((north - bounds[1]) / step))
    if x1 < x0 or y1 < y0:
        raise ValueError("Territory is outside the population grid")
    if (x1-x0+1) * (y1-y0+1) > 100000:
        raise ValueError("Territory exceeds the population analysis limit; select a smaller area")
    available, total = set(manifest["tiles"]), 0.0
    prepared = prep(polygon)
    for y in range(y0, y1+1):
        for x in range(x0, x1+1):
            name = f"{x//size}-{y//size}"
            if name not in available:
                continue  # Sparse grid cells omitted by the dataset are zero.
            values = _tile(str(root), name, size)  # A listed missing tile is an error.
            value = values[(y % size) * size + x % size]
            if value <= 0:
                continue
            cell_west, cell_north = west+x*step, north-y*step
            cell = box(cell_west, cell_north-step, cell_west+step, cell_north)
            if not prepared.intersects(cell):
                continue
            fraction = 1.0 if prepared.covers(cell) else polygon.intersection(cell).area / cell.area
            total += value * max(0.0, min(1.0, fraction))
    # All polygons and holes are handled by the same intersection. No sampling
    # points, map viewport, enclosing rectangle, or individual-area totals.
    analysis = {
        "coverage": "whole_selected_boundary",
        "method": "Population grid counts weighted by polygon-cell intersection area",
        "boundary_sha256": hashlib.sha256(json.dumps(geometry, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "polygon_count": len(polygon.geoms) if polygon.geom_type == "MultiPolygon" else 1,
        "hole_count": sum(len(p.interiors) for p in polygon.geoms) if polygon.geom_type == "MultiPolygon" else len(polygon.interiors),
        "grid_year": manifest["year"],
        "assumption": "Population is distributed uniformly within each aggregated grid cell; model estimate, not an exact resident count",
    }
    return round(total), {**manifest, "analysis": analysis}



def record(key, label, value, unit, confidence, reason, method, scope, sources, period,
           status="available", retrieved_at=None):
    return dict(key=key, label=label, value=value, unit=unit, confidence=confidence,
        confidence_reason=reason, method=method, geographic_scope=scope, sources=sources,
        data_period=period, retrieved_at=retrieved_at or stamp(), status=status)


def missing(key, label, reason, sources=None):
    return record(key, label, None, "", "unverified", reason, "No value inferred", "Unknown",
                  sources or [], "Unknown", status="unavailable")


def source_error(exc):
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        return f"Publisher denied automated retrieval (HTTP {code})" if code in {401,403} else f"Publisher temporarily unavailable (HTTP {code})"
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return "Publisher could not be reached within the research time limit"
    return str(exc)[:250] if isinstance(exc, ValueError) else "The source could not be verified during this run"


def classify_operators(operators, polygon):
    """Attribute every lead to the full polygon; never count nearby or own-brand leads."""
    result, seen = [], {}
    for raw in operators:
        item = dict(raw)
        lat, lon = item.get('latitude', item.get('lat')), item.get('longitude', item.get('lon'))
        located = (isinstance(lat, (int, float)) and not isinstance(lat, bool)
                   and isinstance(lon, (int, float)) and not isinstance(lon, bool)
                   and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)
        item['territory_relation'] = ('inside' if polygon.covers(Point(lon, lat)) else 'outside / nearby') if located else 'unlocated lead; not counted inside territory'
        name = re.sub(r'[^a-z0-9]', '', str(item.get('name', '')).lower())
        item['business_role'] = 'Martins network listing; not counted as an external competitor' if name.startswith(('martinsfuneral', 'martinsbegrafnis')) else 'External provider lead; operating status unverified'
        item['counted_inside'] = item['territory_relation'] == 'inside' and item['business_role'].startswith('External')
        # Exact source or same name at the same mapped point only; branches remain distinct.
        keys = [('source', item['source'])] if item.get('source') else []
        if located and name: keys.append(('location', name, round(lat, 6), round(lon, 6)))
        previous = next((seen[k] for k in keys if k in seen), None)
        if previous is not None:
            sources = previous.setdefault('additional_sources', [])
            if item.get('source') and item['source'] != previous.get('source') and item['source'] not in sources: sources.append(item['source'])
            if previous['territory_relation'].startswith('unlocated') and located:
                sources = previous.get('additional_sources', [])
                previous.update(item)
                previous['additional_sources'] = sources
            for key in keys: seen[key] = previous
            continue
        result.append(item)
        for key in keys: seen[key] = item
    summary = dict(inside_external=sum(o['counted_inside'] for o in result),
        nearby=sum(o['territory_relation'] == 'outside / nearby' for o in result),
        unlocated=sum(o['territory_relation'].startswith('unlocated') for o in result),
        martins_network=sum(o['business_role'].startswith('Martins') for o in result), total_leads=len(result))
    return result, summary


def research(geometry, province, municipality_code, household_size, market_share, title="", area_names=None, ocr_enabled=None, progress=lambda *_: None):
    """Return evidence without changing any franchise or financial input."""
    records, warnings, updates = [], [], {}
    progress(10, "Aggregating the territory population grid")
    try:
        population, manifest = population_in_geometry(geometry)
        updates["population"] = population
        records.append(record("population", "Modelled resident population", population, "people", "medium",
            "500 m aggregation and boundary-cell allocation; not a census count", "Population grid counts weighted by polygon-cell intersection area",
            "Selected territory", [manifest["source"]], "2026 projection; WorldPop R2025A v1", status="cached_reference"))
    except (OSError, ValueError, KeyError) as exc:
        records.append(missing("population", "Modelled resident population", str(exc)))
        population = None
    reference = json.loads((DATA / "territory_reference.json").read_text(encoding="utf8"))
    census = reference["census2022"].get(province)
    if census:
        # Published household sizes are fixed census facts, not refreshed counts.
        updates["household_size"] = census["household_size"]
        if population is not None:
            records.append(record("households", "Estimated households", round(population / census["household_size"]),
                "households", "low", "Province-wide 2022 household size applied to a 2026 population model",
                f"Population / {census['household_size']} persons per household", "Selected territory, provincial proxy",
                [CENSUS, reference["worldpop"]], "2022 household-size reference / 2026 model", status="cached_reference"))
        growth = ((census["population_2022"] / census["population_2011"]) ** (1/11) - 1)*100
        records.append(record("growth", "Historical provincial population growth", round(growth, 3), "% per year",
            "low", "Historical provincial CAGR is context, not a territory development forecast",
            "(Census 2022 / Census 2011)^(1/11) − 1", province, [CENSUS], "2011–2022", status="cached_reference"))
    else:
        records.extend([missing("households", "Estimated households", "Province could not be established"),
                        missing("growth", "Historical growth context", "Province could not be established")])
    progress(30, "Checking Stats SA mortality publications")
    mortality_evidence=None
    try:
        death_pages, death_meta = pdf_text(DEATHS)
        pop_pages, pop_meta = pdf_text(POPULATION)
        deaths_text = "\n".join(p for p in death_pages if "3.6" in p and "residence" in p.lower())
        # Extractors must recognise the table structure; never guess a death rate
        # if the publisher changes it. The curated Compass proxy remains labelled.
        deaths = usual_residence_deaths(deaths_text, province)
        totals = provincial_population("\n".join(p for p in pop_pages if "Appendix 1" in p), province)
        rate = deaths / totals * 1000
        if not 0 < rate < 50 or str(totals).replace(" ", "") not in "".join(pop_pages).replace(" ", ""):
            raise ValueError("Population denominator was not verified in the source")
        status, reason, retrieved = "available", "Provincial registered deaths applied to a custom territory; not age adjusted", death_meta["retrieved_at"]
        mortality_evidence={'death_year':2024,'population_year':2024,'population_series':2024,
            'resident_deaths':deaths,'denominator':totals,'documents':[death_meta,pop_meta]}
    except Exception as exc:
        failure=source_error(exc)
        try:
            from app.franchise.territory_official_reference import mortality_reference
            official=mortality_reference(province)
            rate,status,reason,retrieved=official['rate'],official['status'],official['reason'],official['retrieved_at']
            mortality_evidence=official['provenance']
            warnings.append('Stats SA live refresh unavailable ('+failure+'); the verified archived official release is used, with its original verification date.')
        except (OSError,ValueError,KeyError):
            warnings.append('Stats SA live refresh and verified archive unavailable; imported Compass reference remains unverified.')
            deaths = reference['deaths2024'].get(province)
            total = reference['population2024'].get(province)
            rate = deaths / total * 1000 if deaths and total else None
            status,reason,retrieved='cached_unverified','Imported Compass provincial mortality proxy; official verification unavailable',stamp()
    if rate is not None:
        updates["mortality_per_1000"] = round(rate, 6)
        records.append(record("mortality_per_1000", "Provincial mortality proxy", round(rate, 6), "per 1,000 per year",
            "low", reason, "2024 deaths by usual residence / 2024 provincial population × 1,000", province,
            [DEATHS, POPULATION], "2024", status=status, retrieved_at=retrieved))
        records[-1]['provenance']=mortality_evidence
        if population is not None:
            annual = population * rate / 1000
            records.extend([record("demand", "Estimated annual resident deaths", round(annual, 1), "deaths/year", "low",
                reason, "Modelled territory population × provincial mortality proxy", "Selected territory, provincial mortality proxy",
                [DEATHS, POPULATION, reference["worldpop"]], "2024 mortality / 2026 model", status=status),
                record("capturable_services", "Estimated monthly franchise services", round(annual*market_share/100/12, 2),
                "services/month", "low", "Market share is a user assumption, not researched demand",
                f"Estimated annual deaths × {market_share}% / 12", "Selected territory", [DEATHS, reference["worldpop"]],
                "2024 mortality / 2026 model", status=status)])
    else:
        records.append(missing("demand", "Estimated annual resident deaths", "No supported mortality proxy"))
    progress(50, "Retrieving SARS municipality income context")
    # Explicit 2016 municipal merger crosswalk. No fuzzy name matching.
    code = {"GT482": "GT485", "GT483": "GT485"}.get(municipality_code, municipality_code)
    try:
        if not code:
            raise ValueError("Select a verified municipality code; no province average is substituted")
        pages, meta = pdf_text(SARS)
        income = sars_income("\n".join(pages), code)
        records.append(record("affordability", "Average annual taxable income per assessed taxpayer", income["annual_average_taxable_income"],
            "ZAR/year", "low", "Assessed taxpayers exclude many residents; this is not household income or a package affordability limit",
            f"SARS 2025 Tax Statistics, 2024 tax year; municipality {code}; {income['assessed_taxpayers']} assessed taxpayers" +
            (f"; explicit merger crosswalk from {municipality_code}" if code != municipality_code else ""),
            f"Municipality {code} (2017 demarcation); not custom territory", [SARS], "2024 tax year",
            retrieved_at=meta["retrieved_at"]))
    except Exception as exc:
        records.append(missing("affordability", "SARS municipality income context", source_error(exc), [SARS]))
    progress(75, "Checking mapped funeral operators and major roads")
    polygon = shape(geometry)
    w, s, e, n = polygon.bounds
    map_available=False
    try:
        if e-w > 1.2 or n-s > 1.2:
            raise ValueError("Select a smaller area for bounded road/directory research")
        query = f'[out:json][timeout:15];(nwr[shop=funeral_directors]({s},{w},{n},{e});nwr[amenity=funeral_hall]({s},{w},{n},{e});nwr[amenity=crematorium]({s},{w},{n},{e});way[highway~"^(motorway|trunk|primary|secondary)$"]({s},{w},{n},{e}););out center geom 500;'
        data=mapped_data(query,[w,s,e,n])
        if data.get('extract_fallback'):
            warnings.append('Overpass unavailable; a cached, bounded OpenStreetMap extract supplied the same selected-territory road and listing check.')
        map_source=data['source_url'];map_available=True
        map_meta=data.get('_source_meta',{});map_status='cached_reference' if map_meta.get('cached') else 'available'
        map_checked=map_meta.get('retrieved_at')
        from shapely.geometry import LineString
        operators, roads, seen = [], [], set()
        for item in data.get("elements", []):
            tags = item.get("tags", {})
            identity = f"{item['type']}/{item['id']}"
            if identity in seen: continue
            seen.add(identity)
            if tags.get("highway"):
                coords = [(p["lon"], p["lat"]) for p in item.get("geometry", [])]
                if len(coords) > 1 and polygon.intersects(LineString(coords)):
                    roads.append({"name": tags.get("name") or tags.get("ref") or tags["highway"], "source": "https://www.openstreetmap.org/"+identity})
            else:
                position = item if "lat" in item else item.get("center", {})
                if "lat" in position and polygon.covers(Point(position["lon"], position["lat"])):
                    operators.append({"name": tags.get("name", "Unnamed mapped operator"), "source": "https://www.openstreetmap.org/"+identity,
                        "latitude": position["lat"], "longitude": position["lon"], "verification": "mapped listing; operating status unverified"})
        limited = not data.get("extract_fallback") and len(data.get("elements", [])) >= 500
        records.extend([record("competition", "Mapped funeral-operator listings", len(operators), "listings", "low",
            "Incomplete directory; zero listings never means zero competitors. Operating status requires verification.",
            "Deduplicated OSM objects whose mapped point is inside the selected polygon", "Selected territory",
            [map_source,"https://www.openstreetmap.org/copyright"], "OSM data snapshot",status=map_status,retrieved_at=map_checked),
            record("access", "Mapped major-road segments intersecting territory", len(roads), "segments", "low",
            "Road presence is a proxy; it does not establish journey times, route capacity or access rights",
            "OSM motorway/trunk/primary/secondary lines intersecting the polygon", "Selected territory",
            [map_source,"https://www.openstreetmap.org/copyright"], "OSM data snapshot",status=map_status,retrieved_at=map_checked)])
        if limited: warnings.append("Road/directory response reached the 500-object limit; coverage is incomplete.")
    except Exception as exc:
        operators, roads = [], []
        records.extend([missing("competition", "Mapped funeral operators", source_error(exc), ["https://www.openstreetmap.org/copyright"]),
                        missing("access", "Major-road accessibility context", source_error(exc), ["https://www.openstreetmap.org/copyright"])])
    progress(85, "Running the original Compass business and price research")
    # Reuse the user's working source discovery, public branch and brochure
    # parsers. These run in the Unified worker, not via a localhost companion.
    from app.franchise.compass_research.territory_competitors import lookup
    from app.franchise.compass_research.price_research import research as price_research
    from app.franchise.compass_research.public_web import safe_url, SafeRedirect
    public_prices, original_directory = [], {}
    if title.strip():
        center = polygon.representative_point()
        radius = max(500, min(30000, int(max(e-w, n-s)*111195/2 + 1000)))
        try:
            original_directory = lookup({"title": title, "province": province, "areaNames": area_names or [],
                "lat": center.y, "lon": center.x, "radius": radius})
            for warning in original_directory.get("warnings", []):
                if map_available and warning.startswith("OpenStreetMap directory unavailable"):
                    warning="Additional Compass radius search was unavailable; the selected-boundary OpenStreetMap listing check was retained."
                warnings.append(warning)
            for operator in original_directory.get("competitors", [])[:150]:
                operator["verification"] = operator.get("locationNote", "Public listing; confirm operating status")
                if isinstance(operator.get("lat"), (int, float)) and isinstance(operator.get("lon"), (int, float)):
                    operator["territory_relation"] = "inside" if polygon.covers(Point(operator["lon"], operator["lat"])) else "outside / nearby"
                else: operator["territory_relation"] = "unlocated lead; not counted inside territory"
                if not any(o.get("source") == operator.get("source") for o in operators): operators.append(operator)
            from app.franchise.compass_research.web_price_search import discover
            try:
                discovered_prices = discover({"title": title, "province": province, "areaNames": area_names or []})
                price_sources = [{"name": row["title"], "website": row["url"]} for row in discovered_prices.get("sources", [])]
                warnings.extend(discovered_prices.get("errors", []))
            except Exception:
                price_sources = []
                warnings.append("Compass price-page discovery was unavailable; known business websites were retained.")
            candidates = [o for o in operators if o.get("website") and o.get("territory_relation") != "outside / nearby"] + price_sources
            candidates = candidates[:6]
            if len(operators) + len(price_sources) > 6:
                warnings.append("Automatic brochure/price checks are limited to six source websites per research run; additional business links remain available for review.")
            seen_websites = set()
            for operator in candidates:
                website = operator["website"]
                if website in seen_websites: continue
                seen_websites.add(website)
                try:
                    use_ocr = (os.name == "nt") if ocr_enabled is None else bool(ocr_enabled and os.name == "nt")
                    evidence = price_research(website, safe_url, SafeRedirect, discover=True, ocr=use_ocr)
                    evidence["ocr_available"] = use_ocr
                    public_prices.append({"business": operator.get("name"), "coverage": operator.get("territory_relation", "Indexed price lead; selected-territory coverage unverified"), **evidence})
                except Exception:
                    warnings.append("A Compass price source could not be read; its published link is retained for review.")
        except Exception:
            warnings.append("Original Compass discovery could not complete this run; other evidence is retained.")
    else:
        warnings.append("Enter a town name to enable the original Compass public business/price discovery.")
    operators, competition_summary = classify_operators(operators, polygon)
    if map_available or original_directory.get("source"):
        for entry in records:
            if entry["key"] == "competition":
                entry.update(value=competition_summary['inside_external'], unit="mapped external-provider leads inside territory", status="partial", confidence="low",
                    method=(f"Full selected polygon including exclusions; {competition_summary['inside_external']} external leads inside; "
                            f"{competition_summary['nearby']} nearby; {competition_summary['unlocated']} unlocated; "
                            f"{competition_summary['martins_network']} Martins network listings excluded from external count. "
                            "Exact source/location duplicates merged. Nearby and unlocated leads excluded from inside count."),
                    confidence_reason="Incomplete public directory and web discovery. Zero mapped leads never establishes zero competitors; operating status and service strength need verification.",
                    sources=list(dict.fromkeys([*entry.get('sources', []), *(o['source'] for o in operators if o.get('source'))]))[:20])
    records.append(missing("policy", "Public policy-market evidence", "Public sources do not establish new policy sales or retention. Approved franchise policy cash flow is shown separately in scorecard evidence when a financial baseline is attached."))
    if os.name != "nt":
        warnings.append("Windows OCR is not available on this worker. Image/scanned material retains its original source link for review; text-PDF research continues.")
    return dict(records=records, proposed_inputs=updates, operators=operators, roads=roads, public_prices=public_prices,
        compass_directory=original_directory, competition_summary=competition_summary,
        warnings=warnings, completed_at=stamp(), method_version="martins-research-v1",
        household_size_assumption=household_size, market_share_assumption=market_share)
