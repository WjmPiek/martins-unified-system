"""Durable research orchestration using Unified's existing background queue."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from app.extensions import db
from app.franchise.territory_access import is_territory_owner
from app.franchise.territory_finance import number
from app.jobs import enqueue_job, job_payload, register_job_handler, update_job_progress
from app.models import TerritoryResearchRun, User, Franchise


def context(raw):
    from app.franchise.territory_routes import validate_geometry
    from app.franchise.territory_sources import DATA
    if not isinstance(raw, dict) or set(raw) - {"geometry", "province", "municipality_code", "household_size", "market_share", "title", "area_names"}:
        raise ValueError("Invalid research request")
    if validate_geometry(raw.get("geometry")) is None:
        raise ValueError("Create a territory boundary before researching")
    provinces = json.loads((DATA / "territory_reference.json").read_text())["census2022"]
    if raw.get("province") not in provinces:
        raise ValueError("Select a verified province")
    code = raw.get("municipality_code", "")
    if not isinstance(code, str): raise ValueError("Invalid municipality code")
    code = code.strip()
    if code and not re.fullmatch(r"[A-Z]{2,3}\d{0,3}", code):
        raise ValueError("Enter a municipality code such as GT485, or leave it unknown")
    household = number(raw.get("household_size"), "Household size")
    share = number(raw.get("market_share"), "Market share")
    if not 0 < household <= 30 or not 0 <= share <= 100:
        raise ValueError("Check household-size and market-share assumptions")
    title = raw.get("title", "")
    names = raw.get("area_names", [])
    if not isinstance(title, str) or len(title) > 120 or not isinstance(names, list) or len(names) > 20 or any(not isinstance(n, str) or len(n) > 100 for n in names):
        raise ValueError("Invalid town/suburb research context")
    return {"geometry": raw["geometry"], "province": raw["province"], "municipality_code": code,
            "household_size": float(household), "market_share": float(share), "title": title, "area_names": names}


def fingerprint(raw):
    # Household estimate uses the published provincial size, not this input.
    relevant = {key: raw[key] for key in ("geometry", "province", "municipality_code", "market_share", "title", "area_names")}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def start(franchise_id, actor_id, raw):
    if not actor_allowed(db.session.get(User, actor_id), franchise_id):
        raise ValueError("Research permission was revoked or access is restricted")
    requested = context(raw)
    digest = fingerprint(requested)
    # Serialize starts for the same franchise on PostgreSQL, including a first
    # run with no existing research row to lock.
    db.session.query(Franchise).filter_by(id=franchise_id).with_for_update().one()
    # Durable deduplication and throttling: retry by reusing a fresh result, or
    # resume an active job. Failed runs can be retried after the short cooldown.
    recent = TerritoryResearchRun.query.filter_by(franchise_id=franchise_id).order_by(TerritoryResearchRun.id.desc()).first()
    if recent:
        created = recent.created_at.replace(tzinfo=timezone.utc) if recent.created_at.tzinfo is None else recent.created_at
        age = datetime.now(timezone.utc) - created
        if recent.input_hash == digest and recent.status in {"queued", "running"}:
            return recent
        if recent.input_hash == digest and recent.status == "completed" and age < timedelta(hours=24):
            result = json.loads(recent.result_json or "{}")
            if not any(r.get("status") in {"unavailable", "cached_unverified"} for r in result.get("records", []) if r.get("key") != "policy"):
                return recent
        if age < timedelta(seconds=20):
            raise ValueError("Research was requested recently; wait 20 seconds before retrying")
    run = TerritoryResearchRun(franchise_id=franchise_id, created_by_id=actor_id,
        input_hash=digest, request_json=json.dumps(requested), status="queued")
    db.session.add(run)
    db.session.flush()
    # enqueue_job commits; creating the run first ensures workers can see it.
    job = enqueue_job("territory_public_research", filename="Public territory research",
                      payload={"research_run_id": run.id}, created_by_id=actor_id, queue_name="territory")
    run.job_id = job.id
    db.session.commit()
    return run


def actor_allowed(actor, franchise_id):
    return bool(is_territory_owner(actor) and actor.can_access_franchise(franchise_id)
        and actor.has_permission("franchise_details:view") and actor.has_permission("franchise_details:edit")
        and any(actor.has_role(r) for r in ("Admin", "Super Admin", "Finance Manager")))


@register_job_handler("territory_public_research")
def run_public_research(job):
    from flask import current_app
    from app.franchise.territory_sources import research
    run = db.session.get(TerritoryResearchRun, job_payload(job).get("research_run_id"))
    if not run:
        raise ValueError("Research request no longer exists")
    actor = db.session.get(User, run.created_by_id)
    if not current_app.config.get("TERRITORY_VALUATION_ENABLED") or not actor_allowed(actor, run.franchise_id):
        run.status = "cancelled"
        db.session.commit()
        raise ValueError("Research permission was revoked or the feature was disabled")
    # Check authorization even on retries of completed jobs.
    if run.status == "completed" and run.result_json:
        return {"research_run_id": run.id, "status": "completed"}
    run.status = "running"
    db.session.commit()
    def check_actor():
        # Long public-source requests may outlast a permission/account change.
        db.session.expire_all()
        if not current_app.config.get("TERRITORY_VALUATION_ENABLED") or not actor_allowed(db.session.get(User, run.created_by_id), run.franchise_id):
            raise PermissionError("Research access was revoked while the job was running")

    def progress(step, message):
        check_actor()
        update_job_progress(job, step, message, commit=True)

    try:
        request_data = context(json.loads(run.request_json))
        result = research(**request_data, ocr_enabled=current_app.config.get("TERRITORY_RESEARCH_OCR_ENABLED"), progress=progress)
        check_actor()
        run.result_json = json.dumps(result, allow_nan=False)
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        db.session.commit()
    except PermissionError:
        db.session.rollback()
        run.status = "cancelled"
        job.status = "cancelled"
        job.message = "Research access was revoked while the job was running"
        db.session.commit()
        return {"research_run_id":run.id,"status":"cancelled"}
    except Exception:
        db.session.rollback()
        run.status = "failed"
        db.session.commit()
        raise
    return {"research_run_id": run.id, "status": run.status}


def attach_result(franchise_id, payload):
    run_id = payload.get("research_run_id")
    if run_id is None:
        if payload.get("service_price_selections"):
            raise ValueError("Attach the research run containing the selected cash package")
        return None
    run = db.session.get(TerritoryResearchRun, run_id)
    if not run or run.franchise_id != franchise_id or run.status != "completed":
        raise ValueError("Research result is not available for this franchise")
    original = json.loads(run.request_json)
    expected = {**original, "geometry": payload["geometry"], "province": payload.get("area_province") or original["province"], "market_share": float(number(payload["inputs"].get("market_share", 10), "Market share"))}
    if fingerprint(expected) != fingerprint(original):
        raise ValueError("Boundary, province or market-share assumption changed. Run research again before saving.")
    age = datetime.now(timezone.utc) - (run.finished_at.replace(tzinfo=timezone.utc) if run.finished_at.tzinfo is None else run.finished_at)
    result = json.loads(run.result_json)
    # Reattribute legacy run leads for a new calculation without altering saved research/snapshots.
    from app.franchise.territory_sources import classify_operators
    from shapely.geometry import shape
    result['operators'], summary = classify_operators(result.get('operators', []), shape(payload['geometry']))
    result['competition_summary'] = summary
    for finding in result.get('records', []):
        if finding.get('key') == 'policy':
            finding.update(label='Public policy-market evidence', confidence_reason='Public sources do not establish conversion or retention. Approved franchise policy cash flow is shown separately in scorecard evidence when a financial baseline is attached.')
        if finding.get('key') == 'competition' and finding.get('value') is not None:
            finding.update(value=summary['inside_external'], unit='mapped external-provider leads inside territory',
                method=(f"Full selected polygon: {summary['inside_external']} external leads inside; {summary['nearby']} nearby; "
                        f"{summary['unlocated']} unlocated; {summary['martins_network']} Martins network listings. "
                        "Nearby, unlocated and Martins network leads excluded from the external inside count; exact duplicates merged."),
                confidence='low', confidence_reason='Incomplete directory; zero mapped leads does not establish zero competitors. Operating status needs verification.')
    from app.franchise.compass_research.price_research import excluded_context
    for source in result.get('public_prices',[]):
        for candidate in source.get('candidates',[]):
            if excluded_context(candidate.get('context','')):
                candidate['excluded']=True
                candidate['kind']='Excluded: insurance / cover or other non-comparable amount'
    selections = {}
    for service_type, selection in payload.get("service_price_selections", {}).items():
        if selection["run_id"] != run.id:
            raise ValueError("Selected service price belongs to an older research run")
        try:
            source = result["public_prices"][selection["source_index"]]
            candidate = source["candidates"][selection["candidate_index"]]
        except (KeyError, IndexError, TypeError):
            raise ValueError("Selected funeral-service price is no longer available") from None
        if candidate.get("excluded") or "cash package candidate" not in candidate.get("kind", "").lower() or candidate.get("serviceType", "burial") != service_type:
            raise ValueError("The selected amount is not a cash funeral-service package")
        selected = {"price": candidate["price"], "source": candidate.get("source", source.get("url")),
            "business": source.get("business"), "checked_at": source.get("checkedAt"), "publication_date": None,
            "status": "Advertised cash package; inclusions and local availability require review",
            "used_in_forecast": number(payload["inputs"].get("burial_price" if service_type == "burial" else "cremation_price"), "Service price") == number(candidate["price"], "Advertised price")}
        selections[service_type] = selected
    overrides = {}
    for key, proposed in result.get("proposed_inputs", {}).items():
        if number(payload["inputs"].get(key, proposed), key) != number(proposed, key):
            overrides[key] = {"researched": proposed, "used_in_forecast": payload["inputs"].get(key)}
    return {"run_id": run.id, "context": original, "stale": age > timedelta(days=30), "overrides": overrides, "selected_service_prices": selections, **result}
