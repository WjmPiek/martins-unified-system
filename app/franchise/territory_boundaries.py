"""Official MDB boundaries, dated Census overlays and territory composition."""
from datetime import date, datetime, timedelta, timezone
import re
import math
from shapely.geometry import mapping, shape, Polygon
from shapely.ops import unary_union
from app.franchise.territory_sources import MAIN, SUB, public_json, stamp

MDB_BASE = "https://services7.arcgis.com/oeoyTUJC8HEeYsRB/arcgis/rest/services/"
MDB_RELEASES = {
    (2021, "municipality"): ("27bbdd5b041b4ba6b5707dfed5aa3923", "LocalMunicipalities2018_Final"),
    (2021, "ward"): ("279fbf82a48f46678ddd498627af3f0a", "MDB_Wards_2020"),
    (2026, "municipality"): ("82c7a6d178454ddcb4dc1ef6bf2a4303", "MDB_L_ocal_Municipalities_2026"),
    (2026, "ward"): ("30b6e281a5074eddbf1a3f82aa00459e", "MDB_Wards_2026"),
}
MDB_2026_EFFECTIVE = date(2026, 11, 4)
PROVINCES = {"EC":"Eastern Cape", "FS":"Free State", "GT":"Gauteng", "GP":"Gauteng",
    "KZN":"KwaZulu-Natal", "LP":"Limpopo", "MP":"Mpumalanga", "NC":"Northern Cape",
    "NW":"North West", "WC":"Western Cape"}


def boundary_json(url, params):
    from flask import current_app, has_app_context
    if not has_app_context() or (current_app.testing and not current_app.config.get('TERRITORY_PREVIEW')): return public_json(url, params)
    from pathlib import Path
    from app.franchise.territory_boundary_cache import cached_boundary_json
    return cached_boundary_json(public_json, url, params, Path(current_app.instance_path)/'territory-boundary-cache')


def mdb_boundaries(query, kind, release="current", today=None):
    today = today or datetime.now(timezone(timedelta(hours=2))).date()
    if release not in {"current", "2026"}:
        raise ValueError("Choose current boundaries or the published 2026 boundaries")
    year = 2026 if release == "2026" or today >= MDB_2026_EFFECTIVE else 2021
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 180:
        raise ValueError("Enter a municipality name or official code, for example Rand West City or GT485")
    item_id, service = MDB_RELEASES[(year, kind)]
    metadata_url = f"https://www.arcgis.com/sharing/rest/content/items/{item_id}"
    item = boundary_json(metadata_url, {"f":"json"})
    service_url = MDB_BASE + service + "/FeatureServer"
    if item.get("owner") != "MDBUser1" or item.get("url") != service_url or item.get("type") != "Feature Service":
        raise ValueError("MDB publication identity changed; source review is required")
    if any(word in (item.get("title", "")+" "+(item.get("snippet") or "")).lower() for word in ["draft", "proposed"]):
        raise ValueError("Draft boundaries cannot be used as a verified published release")
    term = query.split(",")[0].strip().upper().replace("'", "''").replace("%", "").replace("_", "")
    name_field = "Municipali" if kind == "ward" and year == 2021 else "MUNICNAME"
    where = f"UPPER({name_field}) LIKE '%{term}%' OR CAT_B = '{term}'"
    layer = service_url + "/0"
    features, limited, cached_at, retrieved_at = [], False, None, None
    for offset in range(0, 300, 100):
        result = boundary_json(layer+"/query", {"f":"geojson", "where":where, "outFields":"*", "outSR":4326,
            "returnGeometry":"true", "geometryPrecision":6, "maxAllowableOffset":.00001,
            "resultRecordCount":100, "resultOffset":offset, "orderByFields":"FID"})
        cached_at = result.get('_boundary_cache') or item.get('_boundary_cache') or cached_at
        retrieved_at = result.get('_source_meta',{}).get('retrieved_at') or retrieved_at
        if result.get("error"): raise ValueError("MDB could not complete this boundary query")
        batch = result.get("features", [])
        features.extend(batch)
        more = result.get("exceededTransferLimit") or result.get("properties", {}).get("exceededTransferLimit") or len(batch) >= 100
        if not more: break
        limited = offset == 200
    from app.franchise.territory_routes import validate_geometry
    areas, seen = [], set()
    for feature in features:
        p = feature.get("properties", {})
        municipal_code = str(p.get("CAT_B", ""))
        if not re.fullmatch(r"[A-Z]{2,3}\d{0,3}", municipal_code): continue
        code = str(p.get("WardID", "")) if kind == "ward" else municipal_code
        if not code or code in seen: continue
        try: validate_geometry(feature.get("geometry"))
        except ValueError: continue
        seen.add(code)
        municipality = str(p.get("MUNICNAME") or p.get("Municipali") or municipal_code)
        province = str(p.get("PROVINCE") or p.get("Province") or "")
        modified = item.get("modified")
        modified_at = datetime.fromtimestamp(modified/1000, timezone.utc).isoformat() if isinstance(modified, (int,float)) else None
        upcoming = year == 2026 and today < MDB_2026_EFFECTIVE
        label_point=shape(feature['geometry']).representative_point()
        areas.append({"id":f"mdb{year}-{kind}-{code}", "name":f"{municipality} · Ward {p.get('WardNo')}" if kind == "ward" else municipality,
            "kind":kind, "municipality":municipality, "municipality_code":municipal_code, "census_municipality_code":"",
            "municipality_crosswalk":"Official MDB municipal code; match the SARS table by this code, not by the Census internal identifier",
            "province":PROVINCES.get(province, province), "geometry":feature["geometry"], "source":layer,
            "label_point":[label_point.x,label_point.y],
            "publisher":"Municipal Demarcation Board", "metadata_source":metadata_url, "data_date":str(year),
            "effective_from":MDB_2026_EFFECTIVE.isoformat() if year == 2026 else "2021 (publisher release)",
            "publication_modified_at":modified_at, "retrieved_at":retrieved_at or stamp(), "cached_at":cached_at, "upcoming":upcoming,
            "confidence":"high", "confidence_reason":"Verified official MDB publication; franchise agreement rights require separate confirmation",
            "selection":"context"})
    return {"areas":areas, "limited":limited, "message":f"Official MDB {year} {kind} boundaries. " +
        ("Upcoming: effective 4 November 2026. " if year == 2026 and today < MDB_2026_EFFECTIVE else "Currently effective release. ") +
        "Municipal/ward areas are not automatic franchise rights. Population statistics must match their boundary vintage." +
        (" Search reached 300 areas; narrow the municipality name." if limited else "") + (f" Displaying a checked copy stored {cached_at}." if cached_at else "")}


def query_boundaries(query=None, kind="municipality", bounds=None, main_code=None, release="current", point=None):
    if kind == 'auto':
        if not isinstance(query,str) or not 2 <= len(query.strip()) <= 180:
            raise ValueError('Enter a town, suburb or municipality name, or an official municipal code')
        failures=[]
        for search_kind in ['town','municipality','suburb']:
            try:
                found=query_boundaries(query=query,kind=search_kind,release=release)
                if found['areas']:
                    found['matched_kind']=search_kind
                    found['message']='Automatic search matched '+search_kind+' boundaries. '+found['message']
                    if failures:found['message']+=' Another boundary source was unavailable; the matched source is shown.'
                    return found
            except Exception as exc:
                failures.append(type(exc).__name__)
        if len(failures)==3:
            raise RuntimeError('Boundary sources unavailable; retry the search shortly')
        return dict(areas=[],limited=False,message='No matching published boundary found. Try the town, suburb or official municipality name.' + (' Some publishers were unavailable.' if failures else ''))
    if kind in {"municipality", "ward"}:
        return mdb_boundaries(query, kind, release)
    if kind not in {"town", "suburb"}:
        raise ValueError("Choose town or suburb")
    layer = SUB if kind == "suburb" else MAIN
    if point is not None:
        if kind!='suburb' or not isinstance(point,list) or len(point)!=2 or any(type(v) not in {int,float} or not math.isfinite(v) for v in point):
            raise ValueError('Choose a valid point for suburb lookup')
        lon,lat=point
        if not(16<=lon<=34 and -35.3<=lat<=-22):raise ValueError('Click a point inside South Africa')
        filters={'where':'1=1','geometry':f'{lon},{lat}','geometryType':'esriGeometryPoint','inSR':4326,'spatialRel':'esriSpatialRelIntersects'}
    elif query is not None:
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 180:
            raise ValueError("Enter a town or suburb and optional province")
        parts = [p.strip().upper().replace("'", "''").replace("%", "").replace("_", "") for p in query.split(",")]
        field = "SP_NAME" if kind == "suburb" else "MP_NAME"
        where = f"UPPER({field}) LIKE '%{parts[0]}%'"
        if len(parts) > 1 and parts[1]:
            province = parts[1] in {"WESTERN CAPE", "EASTERN CAPE", "NORTHERN CAPE", "FREE STATE", "KWAZULU-NATAL", "NORTH WEST", "GAUTENG", "MPUMALANGA", "LIMPOPO"}
            where += f" AND UPPER({'PR_NAME' if province else 'MP_NAME'}) LIKE '%{parts[1]}%'"
        filters = {"where": where}
    else:
        if not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) not in {int,float} or not math.isfinite(v) for v in bounds):
            raise ValueError("Choose a town before discovering suburbs")
        w, s, e, n = bounds
        if not (16 <= w < e <= 34 and -35.3 <= s < n <= -22 and e-w <= 1.2 and n-s <= 1.2):
            raise ValueError("Choose a smaller town for suburb discovery")
        filters = {"where": "1=1", "geometry": ",".join(map(str, bounds)), "geometryType": "esriGeometryEnvelope",
                   "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"}
        if main_code:
            if not str(main_code).isdigit():
                raise ValueError("Invalid main-place code")
            filters = {"where": f"MP_CODE = {int(main_code)}"}
    features, limited, cached_at, retrieved_at = [], False, None, None
    for offset in range(0, 300, 100):
        data = boundary_json(layer+"/query", {"f": "geojson", "outFields": "*", "outSR": 4326,
            "returnGeometry": "true", "geometryPrecision": 6, "maxAllowableOffset": .00001,
            "resultRecordCount": 100, "resultOffset": offset, "orderByFields": "OBJECTID", **filters})
        cached_at = data.get('_boundary_cache') or cached_at
        retrieved_at = data.get('_source_meta',{}).get('retrieved_at') or retrieved_at
        if data.get("error"):
            raise ValueError("Boundary publisher could not complete this query")
        batch = data.get("features", [])
        features.extend(batch)
        more = data.get("exceededTransferLimit") or data.get('properties',{}).get('exceededTransferLimit') or len(batch) >= 100
        if not more: break
        limited = offset == 200
    # Import here avoids a circular import while registering the native routes.
    from app.franchise.territory_routes import validate_geometry
    areas, seen = [], set()
    for feature in features:
        p = feature.get("properties", {})
        code = str(p.get("SP_CODE" if kind == "suburb" else "MP_CODE", ""))
        if not code or code in seen: continue
        try: validate_geometry(feature.get("geometry"))
        except ValueError: continue
        seen.add(code)
        municipality_name = str(p.get("MN_NAME", ""))
        # Census 2011 MN_CODE is an internal numeric geography identifier,
        # not the GT/DC municipal code used in the SARS income table.
        sars_code = "GT485" if municipality_name.casefold() in {"westonaria", "randfontein"} else "JHB" if municipality_name.casefold() == "city of johannesburg" else ""
        label_point=shape(feature['geometry']).representative_point()
        areas.append({"id": f"census2011-{kind}-{code}", "name": str(p.get("SP_NAME" if kind == "suburb" else "MP_NAME", "Unnamed area")),
            "description":' · '.join(str(v) for v in [p.get('SP_NAME' if kind=='suburb' else 'MP_NAME'),p.get('MP_NAME') if kind=='suburb' else None,municipality_name,p.get('PR_NAME'),'2011 census boundary'] if v),
            "label_point":[label_point.x,label_point.y],
            "province": p.get("PR_NAME", "").replace("North-West", "North West"), "main_code": str(p.get("MP_CODE", "")),
            "municipality_code": sars_code, "census_municipality_code": str(p.get("MN_CODE", "")), "municipality": municipality_name,
            "municipality_crosswalk": "SARS 2025 labels Randfontein/Westonaria Municipality GT485" if sars_code=="GT485" else "City of Johannesburg matched to official MDB code JHB (checked 21 October 2026)" if sars_code=="JHB" else "SARS code requires verification",
            "geometry": feature["geometry"], "source": layer, "data_date": "2011-10-09",
            "retrieved_at": retrieved_at or stamp(), "cached_at":cached_at, "selection": "context", "kind": kind})
    if query:
        wanted = query.split(",")[0].strip().casefold()
        areas.sort(key=lambda a: (a["name"].casefold() != wanted, a["name"]))
    return {"areas": areas, "limited": limited,
        "message": "Stats SA 2011 census polygons, shared by UCT Libraries. Confirm present-day names and franchise agreement rights." +
        (" Discovery reached the 300-area limit; narrow the search or add suburbs by name." if limited else "") + (f" Displaying a checked copy stored {cached_at}." if cached_at else "")}


def compose(parts, include_enclosed=False):
    from app.franchise.territory_routes import validate_geometry
    if not isinstance(parts, list) or not 1 <= len(parts) <= 150:
        raise ValueError("Select between 1 and 150 geographical parts")
    if type(include_enclosed) is not bool:raise ValueError("Invalid enclosed-area selection")
    include, exclude, normalized, seen = [], [], [], set()
    for part in parts:
        if not isinstance(part, dict) or part.get("selection") not in {"include", "exclude", "context"}:
            raise ValueError("Invalid boundary selection")
        if not isinstance(part.get("id"), str) or len(part["id"]) > 100 or part["id"] in seen:
            raise ValueError("Boundary parts need unique identifiers")
        seen.add(part["id"])
        if not isinstance(part.get("name"), str) or len(part["name"]) > 250:
            raise ValueError("Invalid boundary name")
        geometry = validate_geometry(part.get("geometry"))
        if geometry is None: raise ValueError("Each area needs a polygon")
        if part["selection"] == "include": include.append(shape(geometry))
        if part["selection"] == "exclude": exclude.append(shape(geometry))
        normalized.append(part)
    if not include:
        raise ValueError("Include at least one town or suburb")
    combined = unary_union(include)
    if include_enclosed:
        # Fill only closed interior rings, never bridge detached suburbs or use a hull.
        polygons=list(combined.geoms) if combined.geom_type=='MultiPolygon' else [combined]
        combined=unary_union([Polygon(p.exterior) for p in polygons])
    if exclude: combined = combined.difference(unary_union(exclude))
    result = json_geometry(combined)
    validate_geometry(result)
    return result


def json_geometry(polygon):
    import json
    return json.loads(json.dumps(mapping(polygon)))
