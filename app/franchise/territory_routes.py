"""Native franchise territory workflow. No external app or separate server."""
from datetime import datetime, timezone, timedelta
import json
from secrets import token_urlsafe, compare_digest
from urllib.parse import urlsplit
from flask import abort, current_app, jsonify, render_template, request, session, redirect, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from app.audit import log_action
from app.extensions import db
from app.franchise.routes import franchise_bp
from app.franchise.territory_finance import DEFAULTS, WEIGHTS, calculate, serializable, scorecard, validate_inputs
from app.franchise.territory_access import is_territory_owner, can_view_territories
from app.models import Franchise, FranchiseTerritory, TerritoryValuationSnapshot, TerritoryResearchRun, ImportJob, WorkerHeartbeat


@franchise_bp.context_processor
def territory_field_helpers():
    from app.franchise.territory_fields import display_date, field_spec, field_value, field_label, LABELS, MONTHS
    from app.franchise.territory_finance import COMMERCIAL_FEES
    return dict(territory_commercial_fees=COMMERCIAL_FEES,territory_date=display_date,territory_field=field_spec,territory_value=field_value,territory_label=field_label,territory_labels=LABELS,territory_months=MONTHS)


@franchise_bp.after_request
def territory_private_response(response):
    if (request.endpoint or '').startswith('franchise.territory'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def access(franchise_id, write=False):
    if not current_app.config.get("TERRITORY_VALUATION_ENABLED", False):
        abort(404)
    if not is_territory_owner(current_user):
        abort(403)
    if not current_user.has_permission("franchise_details:view") or not current_user.can_access_franchise(franchise_id):
        abort(403)
    if write:
        if not current_user.has_permission("franchise_details:edit") or not any(current_user.has_role(r) for r in ("Admin", "Super Admin", "Finance Manager")):
            abort(403)
        provided = request.headers.get("X-Territory-CSRF", "")
        expected = session.get("territory_csrf", "")
        if not expected or not compare_digest(provided, expected):
            abort(400, "Refresh the page before saving this study")
    return db.get_or_404(Franchise, franchise_id)


def validate_geometry(geometry):
    if geometry is None:
        return None
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Import a GeoJSON Polygon or MultiPolygon")
    polygons = [geometry.get("coordinates")] if geometry["type"] == "Polygon" else geometry.get("coordinates")
    if not isinstance(polygons, list) or not 1 <= len(polygons) <= 150:
        raise ValueError("Invalid boundary polygons")
    count = 0
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("A boundary must have an outer ring")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Boundary rings must be closed")
            for point in ring:
                count += 1
                if count > 30000 or not isinstance(point, list) or len(point) != 2:
                    raise ValueError("Boundary is too complex")
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in point) or not (16 <= point[0] <= 34 and -35.3 <= point[1] <= -22):
                    raise ValueError("Boundary must be inside South Africa")
    # Shapely provides topology validation, not just coordinate validation.
    from shapely.geometry import shape
    parsed = shape(geometry)
    if not parsed.is_valid or parsed.is_empty or parsed.area <= 0:
        raise ValueError("Boundary crosses itself or has invalid holes")
    return geometry


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) - {"inputs", "scores", "score_evidence", "geometry", "sources", "boundary_note", "boundary_parts", "research_run_id", "service_price_selections", "financial_baseline", "area_linked_figures", "area_province", "include_enclosed", "scenario_mode", "prospect", "comparison_baseline"}:
        raise ValueError("Invalid study data")
    if not isinstance(payload.get("inputs"), dict):
        raise ValueError("Enter financial inputs")
    # Owner salary is a separate manual entry for new calculations. Existing
    # saved snapshots retain their original salary treatment.
    from app.franchise.territory_fields import display_date, separate_owner_salary
    payload={**payload,'inputs':separate_owner_salary(payload['inputs'])}
    validated=validate_inputs(payload["inputs"])
    from app.franchise.territory_finance import COMMERCIAL_FEES
    if validated["fee_method"]=="commercial" and validated["territory_fee"]!=COMMERCIAL_FEES[validated["commercial_category"]]:
        raise ValueError("Apply the selected commercial fee, or choose another fee method for a different amount")
    mode=payload.get('scenario_mode','existing')
    if not isinstance(mode,str) or mode not in {'existing','prospective'}:raise ValueError('Choose an existing or prospective business scenario')
    if mode=='prospective':
        plan=payload.get('prospect')
        if not isinstance(plan,dict) or set(plan)!={'name','royalty_percent'} or not isinstance(plan['name'],str) or not 1<=len(plan['name'].strip())<=180:
            raise ValueError('Name the prospective area or business')
        from app.franchise.territory_finance import number
        if not 0<=number(plan['royalty_percent'],'Planning royalty')<=100:raise ValueError('Planning royalty must be from 0 to 100 percent')
        if payload.get('financial_baseline') or validated['demand_basis']!='territory' or validated['recorded_monthly_services']!=0:
            raise ValueError('Prospective scenarios cannot use a holder financial baseline or recorded activity')
        if not payload.get('area_province'):raise ValueError('Choose the prospective territory province')
    comparison=payload.get('comparison_baseline')
    if comparison is not None and (not isinstance(comparison,str) or len(comparison)!=64 or any(c not in '0123456789abcdef' for c in comparison)):
        raise ValueError('Invalid running-concern comparison reference')
    reference = payload.get("financial_baseline")
    if reference is not None and (not isinstance(reference,str) or len(reference)!=64 or any(c not in '0123456789abcdef' for c in reference)):
        raise ValueError("Invalid franchise financial reference")
    if payload['inputs'].get('demand_basis') == 'franchise_history' and reference is None:
        raise ValueError("Load the approved franchise figures before using recorded activity")
    payload = {"scores": {}, "score_evidence": {}, "geometry": None, "sources": [], "boundary_note": "", **payload}
    validate_geometry(payload["geometry"])
    if 'area_linked_figures' in payload and type(payload['area_linked_figures']) is not bool:
        raise ValueError('Choose whether figures are linked to the territory')
    if payload.get('area_linked_figures') and payload['geometry'] is None:
        raise ValueError('Select an area before calculating territory-linked figures')
    if payload.get('area_province') is not None:
        from app.franchise.territory_boundaries import PROVINCES
        if payload['area_province'] not in set(PROVINCES.values()):raise ValueError('Choose a recognised province')
    if "boundary_parts" in payload and not isinstance(payload["boundary_parts"], list):
        raise ValueError("Boundary parts must be a list")
    if type(payload.get("include_enclosed",False)) is not bool:
        raise ValueError("Invalid enclosed-area selection")
    if payload.get("boundary_parts"):
        from app.franchise.territory_boundaries import compose
        derived = compose(payload["boundary_parts"],include_enclosed=payload.get("include_enclosed",False))
        from shapely.geometry import shape
        if payload["geometry"] is None or not shape(derived).equals(shape(payload["geometry"])):
            raise ValueError("The territory must match its saved suburb selections")
    if payload.get("research_run_id") is not None and (type(payload["research_run_id"]) is not int or payload["research_run_id"] < 1):
        raise ValueError("Invalid research reference")
    selections = payload.get("service_price_selections", {})
    if not isinstance(selections, dict) or set(selections) - {"burial", "cremation"}:
        raise ValueError("Invalid funeral-service price selections")
    for selection in selections.values():
        if not isinstance(selection, dict) or set(selection) != {"run_id", "source_index", "candidate_index"} or any(type(v) is not int or v < 0 for v in selection.values()):
            raise ValueError("Select a funeral-service price from a recorded research finding")
    if not isinstance(payload["boundary_note"], str) or len(payload["boundary_note"]) > 5000:
        raise ValueError("Boundary note is too long")
    if not isinstance(payload["sources"], list) or len(payload["sources"]) > 100:
        raise ValueError("Invalid evidence sources")
    for source in payload["sources"]:
        if not isinstance(source, dict) or set(source) != {"metric", "url", "data_date", "retrieved_at", "confidence", "method", "value"}:
            raise ValueError("Each source needs metric, URL, data date, retrieval date, confidence, method and value")
        if any(not isinstance(v, str) or len(v) > 4000 for v in source.values()):
            raise ValueError("Evidence fields must be text within 4000 characters")
        url = urlsplit(source["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Evidence links must be public HTTPS URLs")
        if source["confidence"] not in {"high", "medium", "low", "unverified"}:
            raise ValueError("Choose a source confidence")
        for field in ("data_date", "retrieved_at"):
            try:
                stamp = datetime.fromisoformat(source[field].replace("Z", "+00:00"))
                if stamp.date() > datetime.now(timezone.utc).date():
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError("Source dates must be valid and cannot be in the future") from None
    if not isinstance(payload["score_evidence"], dict) or any(not isinstance(v, str) or len(v) > 4000 for v in payload["score_evidence"].values()):
        raise ValueError("Invalid score evidence")
    if set(payload["score_evidence"]) - set(WEIGHTS):
        raise ValueError("Invalid score evidence factors")
    scorecard(payload["scores"], payload["score_evidence"])
    return payload


def financial_access(payload):
    if (payload.get('financial_baseline') or payload.get('comparison_baseline')) and not current_user.has_permission('monthly_figures:view'):
        abort(403)


def evaluate(franchise, payload):
    from app.franchise.territory_research import attach_result
    research = attach_result(franchise.id, payload)
    area_estimate=None
    if payload.get('geometry'):
        payload['area_linked_figures']=True
        from app.franchise.territory_area_estimate import estimate
        area_estimate=estimate(payload['geometry'],payload.get('area_province') or franchise.province,research)
        payload['inputs']={**payload['inputs'],**area_estimate['inputs']}
    result = {}
    actuals=None
    if payload.get('financial_baseline'):
        financial_access(payload)
        from app.franchise.territory_actuals import baseline
        actuals=baseline(franchise.id)
        if actuals['fingerprint']!=payload['financial_baseline']:
            raise ValueError('Approved franchise figures changed. Reload the franchise baseline before saving.')
        if not actuals['proposed_inputs']:
            raise ValueError('Franchise history is insufficient for a financial baseline')
        actuals['overrides']={k:{'recorded_baseline':v,'forecast':payload['inputs'].get(k)} for k,v in actuals['proposed_inputs'].items()
            if k!='confirmed' and payload['inputs'].get(k)!=v}
        result['financial_baseline']=actuals
    if payload.get('comparison_baseline'):
        financial_access(payload)
        from app.franchise.territory_actuals import baseline
        from app.franchise.territory_prospects import running_concern_summary
        comparison=baseline(franchise.id)
        if comparison['fingerprint']!=payload['comparison_baseline']:raise ValueError('Running-concern records changed. Refresh the separate comparison before saving.')
        result['running_concern']=running_concern_summary(comparison,franchise.business_name,franchise)
    elif actuals:
        from app.franchise.territory_prospects import running_concern_summary
        result['running_concern']=running_concern_summary(actuals,franchise.business_name,franchise)
    from app.franchise.territory_score_guidance import recommendations, populate_suggestions
    guidance_research=research
    if area_estimate:
        guidance_research={**(research or {}),'records':list({r['key']:r for r in [*(research or {}).get('records',[]),*area_estimate['records']]}.values())}
        result['area_estimate']=area_estimate
        result['calculated_inputs']=area_estimate['inputs']
    result['score_recommendations']=recommendations(payload,guidance_research,actuals)
    populate_suggestions(payload,result['score_recommendations'])
    prospective=payload.get('scenario_mode')=='prospective'
    result.update(calculate(None if prospective else franchise, payload['inputs'], payload['scores'], payload['score_evidence'],
        planning_royalty=payload['prospect']['royalty_percent'] if prospective else None))
    if prospective:
        from app.franchise.territory_prospects import assumption_ledger
        result['prospective']=assumption_ledger(payload,area_estimate,research)
    result['calculated_scores']=payload['scores']
    result['calculated_score_evidence']=payload['score_evidence']
    result["research"] = research
    result["selected_service_prices"] = research.get("selected_service_prices", {}) if research else {}
    complete = bool(payload["geometry"]) and bool(payload["boundary_note"].strip()) and all(
        any(s["metric"] == key and s["confidence"] in {"high", "medium"} and s["method"].strip() and s["value"].strip()
            for s in payload["sources"]) for key in WEIGHTS)
    assumed_factors = [key for key, entry in result['score_recommendations']['entries'].items()
        if entry.get('status') == 'planning_assumption'
        and str(payload['score_evidence'].get(key, '')).startswith('Automatic suggestion:')
        and str(payload['scores'].get(key)) == str(entry['recommended_score'])]
    result['assumed_score_factors'] = assumed_factors
    if assumed_factors: complete = False
    result["evidence_complete"] = complete
    result["status"] = "draft"
    if research and research["stale"]:
        complete = False
        result["evidence_complete"] = False
    if area_estimate and not area_estimate['mortality_verified']:
        complete=False
        result['evidence_complete']=False
    if not complete:
        result["recommended_fee"] = None
        result["recommendation_reason"] = "Record a sourced boundary and current evidence of medium or high confidence for every scorecard factor before recommending a price."
    if assumed_factors:
        result['recommendation_reason']='All seven planning scores are populated. Neutral 3/5 assumptions still need verified local evidence and owner review before recommending a territory price.'
    if area_estimate and not area_estimate['mortality_verified']:
        result['recommendation_reason']='Area figures use the dated Compass mortality reference. Refresh and verify the mortality source and complete the scorecard before recommending a territory price.'
    from app.franchise.territory_assessments import build_assessments
    result['assessments']=build_assessments(franchise,payload,result)
    return result


@franchise_bp.get("/territories")
@login_required
def territory_index():
    if not current_app.config.get("TERRITORY_VALUATION_ENABLED", False):
        abort(404)
    if not can_view_territories(current_user):
        abort(403)
    franchises = current_user.accessible_franchises()
    if request.args.get('choose') != '1' and franchises:
        accessible_ids = {franchise.id for franchise in franchises}
        remembered = session.get('territory_workspace', {})
        selected = remembered.get('franchise_id') if remembered.get('user_id') == current_user.id else None
        if selected not in accessible_ids:
            latest = FranchiseTerritory.query.filter(
                FranchiseTerritory.updated_by_id == current_user.id,
                FranchiseTerritory.franchise_id.in_(accessible_ids),
            ).order_by(FranchiseTerritory.updated_at.desc(), FranchiseTerritory.id.desc()).first()
            selected = latest.franchise_id if latest else current_user.assigned_franchise_id()
        if selected not in accessible_ids:
            selected = franchises[0].id
        return redirect(url_for('franchise.territory', franchise_id=selected, assessment='prospective'))
    return render_template("franchise/territories.html",
                           franchises=franchises)


@franchise_bp.get("/<int:franchise_id>/territory")
@login_required
def territory(franchise_id):
    franchise = access(franchise_id)
    study = FranchiseTerritory.query.filter_by(franchise_id=franchise_id).first()
    session.setdefault("territory_csrf", token_urlsafe(32))
    payload = json.loads(study.payload_json) if study else {"inputs": {**DEFAULTS,"expense_mode":"percentage","expense_percent":70,"salary_in_fixed":False}, "scores": {}, "score_evidence": {}, "geometry": None, "sources": [], "boundary_note": ""}
    fresh_prospect=request.args.get('scenario')=='prospective' or not study
    if fresh_prospect:
        from app.franchise.territory_prospects import suggested_inputs
        payload=dict(inputs=suggested_inputs(),scenario_mode='prospective',prospect={'name':'New prospective territory','royalty_percent':6},
            scores={},score_evidence={},sources=[],geometry=None,boundary_note='',area_province='Gauteng')
    financial_access(payload)
    salary_conversion_note=''
    if study and payload['inputs'].get('salary_in_fixed',True):
        from app.franchise.territory_fields import display_date, separate_owner_salary
        payload['inputs']=separate_owner_salary(payload['inputs'])
        payload['inputs']['confirmed']=False
        salary_conversion_note='The previous fixed-cost amount has been split into other fixed costs and owner salary. Review the separate entries before saving; the combined cost is counted once.'
    if not study:
        from app.franchise.territory_score_guidance import recommendations, populate_suggestions
        populate_suggestions(payload, recommendations(payload))
    history = TerritoryValuationSnapshot.query.filter_by(territory_id=study.id).order_by(TerritoryValuationSnapshot.version.desc()).limit(20).all() if study else []
    editable = current_user.has_permission("franchise_details:edit") and any(current_user.has_role(r) for r in ("Admin", "Super Admin", "Finance Manager"))
    session['territory_workspace'] = {'user_id': current_user.id, 'franchise_id': franchise.id}
    from app.franchise.territory_score_guidance import LABELS,GUIDANCE
    return render_template("franchise/territory.html", franchise=franchise, payload=payload,
        defaults=DEFAULTS, salary_conversion_note=salary_conversion_note, weights=WEIGHTS, factor_labels=LABELS, factor_guidance=GUIDANCE, version=study.version if study else 0, history=history,
        saved_result=presentation_result(json.loads(history[0].result_json)) if history and not fresh_prospect else None,
        editable=editable, csrf=session["territory_csrf"], maps_key=current_app.config.get("GOOGLE_MAPS_API_KEY", ""))


def presentation_result(result):
    from app.franchise.territory_assessments import safe_link
    data=serializable(result)
    data['assessment_html']=render_template('franchise/_territory_assessment_panels.html',assessments=data.get('assessments',{}),safe_link=safe_link)
    return data


@franchise_bp.post("/<int:franchise_id>/territory/calculate")
@login_required
def territory_calculate(franchise_id):
    franchise = access(franchise_id, write=True)
    if request.content_length is None or request.content_length > 2000000:
        abort(413)
    try:
        payload = validate_payload(request.get_json())
        result = evaluate(franchise, payload)
        return jsonify(presentation_result(result))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@franchise_bp.post('/<int:franchise_id>/territory/score-recommendations')
@login_required
def territory_score_recommendations(franchise_id):
    franchise=access(franchise_id,write=True)
    if request.content_length is None or request.content_length>2000000:abort(413)
    try:
        result=evaluate(franchise,validate_payload(request.get_json()))
        response=jsonify(serializable(result['score_recommendations']))
        response.headers['Cache-Control']='no-store'
        return response
    except ValueError as exc:
        return jsonify(error=str(exc)),400


@franchise_bp.post("/<int:franchise_id>/territory/save")
@login_required
def territory_save(franchise_id):
    franchise = access(franchise_id, write=True)
    if request.content_length is None or request.content_length > 2000000:
        abort(413)
    body = request.get_json()
    if not isinstance(body, dict) or not isinstance(body.get("version"), int) or isinstance(body.get("version"), bool):
        return jsonify(error="A study version is required"), 400
    try:
        payload = validate_payload(body.get("payload"))
        result = serializable(evaluate(franchise, payload))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    raw = json.dumps(payload, allow_nan=False)
    study = FranchiseTerritory.query.filter_by(franchise_id=franchise_id).first()
    if not study:
        if body["version"] != 0:
            return jsonify(error="Study changed. Reload before saving."), 409
        study = FranchiseTerritory(franchise_id=franchise_id, version=1, payload_json=raw, updated_by_id=current_user.id)
        db.session.add(study)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="Study was created in another session. Reload before saving."), 409
    else:
        expected = body["version"]
        updated = db.session.query(FranchiseTerritory).filter_by(id=study.id, version=expected).update(
            {"version": expected + 1, "payload_json": raw, "updated_at": datetime.now(timezone.utc), "updated_by_id": current_user.id})
        if updated != 1:
            db.session.rollback()
            return jsonify(error="Study changed. Reload before saving."), 409
        db.session.flush()
    snapshot = TerritoryValuationSnapshot(territory_id=study.id, version=study.version, payload_json=raw,
        result_json=json.dumps(result), created_by_id=current_user.id)
    db.session.add(snapshot)
    log_action("Franchise Details", "Saved territory planning revision", f"Franchise {franchise_id}; revision {study.version}")
    db.session.commit()
    return jsonify(version=study.version)


@franchise_bp.get("/<int:franchise_id>/territory/export")
@login_required
def territory_export(franchise_id):
    access(franchise_id)
    if not current_user.has_permission("franchise_details:export"):
        abort(403)
    study = FranchiseTerritory.query.filter_by(franchise_id=franchise_id).first_or_404()
    version = request.args.get("version", study.version, type=int)
    snapshot = TerritoryValuationSnapshot.query.filter_by(territory_id=study.id, version=version).first_or_404()
    financial_access(json.loads(snapshot.payload_json))
    response = jsonify(franchise_id=franchise_id, version=version, created_at=snapshot.created_at.isoformat(),
        payload=json.loads(snapshot.payload_json), result=json.loads(snapshot.result_json))
    response.headers["Content-Disposition"] = f'attachment; filename="territory-{franchise_id}-v{version}.json"'
    response.headers["Cache-Control"] = "no-store"
    return response


@franchise_bp.post('/<int:franchise_id>/territory/prospect-suggestions')
@login_required
def territory_prospect_suggestions(franchise_id):
    franchise=access(franchise_id,write=True)
    if request.content_length is None or request.content_length>2000000:abort(413)
    from app.franchise.territory_prospects import suggested_inputs
    try:
        body=request.get_json()
        if not isinstance(body,dict) or not body.get('geometry'):raise ValueError('Select the prospective territory before generating area suggestions')
        public={key:body[key] for key in ['geometry','boundary_parts','boundary_note','area_province','include_enclosed'] if key in body}
        title=body.get('name') or 'Prospective territory'
        data=validate_payload(dict(**public,inputs=suggested_inputs(),scenario_mode='prospective',prospect={'name':title,'royalty_percent':6},
            financial_baseline=None,comparison_baseline=None,research_run_id=None,service_price_selections={},scores={},score_evidence={},sources=[]))
        result=evaluate(franchise,data)
        response=jsonify({'payload':serializable(data),'result':presentation_result(result)})
        response.headers['Cache-Control']='no-store'
        return response
    except (ValueError,TypeError) as exc:return jsonify(error=str(exc)),400


@franchise_bp.post('/<int:franchise_id>/territory/franchise-figures')
@login_required
def territory_franchise_figures(franchise_id):
    access(franchise_id,write=True)
    if not current_user.has_permission('monthly_figures:view'): abort(403)
    from app.franchise.territory_actuals import baseline
    try:
        response=jsonify(baseline(franchise_id))
        response.headers['Cache-Control']='no-store'
        return response
    except ValueError as exc:
        return jsonify(error=str(exc)),400


@franchise_bp.post("/<int:franchise_id>/territory/boundaries")
@login_required
def territory_boundaries(franchise_id):
    access(franchise_id, write=True)
    from app.franchise.territory_boundaries import query_boundaries
    try:
        body = request.get_json()
        if not isinstance(body, dict) or request.content_length is None or request.content_length > 10000:
            raise ValueError("Invalid boundary search")
        result = query_boundaries(query=body.get("query"), kind=body.get("kind", "municipality"),
                                  bounds=body.get("bounds"), main_code=body.get("main_code"), release=body.get("release", "current"),point=body.get('point'))
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        return jsonify(error="Boundary sources are temporarily unavailable and no recent checked copy is available. Retry shortly, or import a boundary with its source and date."), 503


@franchise_bp.post("/<int:franchise_id>/territory/compose")
@login_required
def territory_compose(franchise_id):
    access(franchise_id, write=True)
    from app.franchise.territory_boundaries import compose
    try:
        if request.content_length is None or request.content_length > 2000000: abort(413)
        body = request.get_json()
        geometry = compose(body.get("parts"),include_enclosed=body.get("include_enclosed",True))
        return jsonify(geometry=geometry)
    except (ValueError, AttributeError) as exc:
        return jsonify(error=str(exc)), 400


@franchise_bp.post("/<int:franchise_id>/territory/research")
@login_required
def territory_start_research(franchise_id):
    access(franchise_id, write=True)
    from app.franchise.territory_research import start
    try:
        if request.content_length is None or request.content_length > 2000000: abort(413)
        run = start(franchise_id, current_user.id, request.get_json())
        return jsonify(run_id=run.id, status=run.status), 202
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@franchise_bp.get("/<int:franchise_id>/territory/research/<int:run_id>")
@login_required
def territory_research_status(franchise_id, run_id):
    access(franchise_id)
    run = TerritoryResearchRun.query.filter_by(id=run_id, franchise_id=franchise_id).first_or_404()
    job = db.session.get(ImportJob, run.job_id) if run.job_id else None
    effective_status = run.status
    if job and run.status not in {"completed", "cancelled"}:
        effective_status = job.status
    active_worker = WorkerHeartbeat.query.filter(
        WorkerHeartbeat.queue_name == (job.queue_name if job else 'default'),
        WorkerHeartbeat.status.in_(['idle', 'running', 'polling']),
        WorkerHeartbeat.heartbeat_at >= datetime.now(timezone.utc)-timedelta(seconds=90)).first() is not None
    message = job.message if job else "Waiting for a background worker"
    if effective_status == 'queued' and not active_worker:
        message = "Queued. No active Unified background worker is reporting; start the existing job worker to process research."
    response = jsonify(run_id=run.id, status=effective_status,
        progress=job.progress_percent if job else 0,
        message=message, worker_available=active_worker,
        context=json.loads(run.request_json), result=json.loads(run.result_json) if run.result_json else None)
    response.headers["Cache-Control"] = "no-store"
    return response


@franchise_bp.get("/<int:franchise_id>/territory/report")
@login_required
def territory_report(franchise_id):
    franchise = access(franchise_id)
    if not current_user.has_permission("franchise_details:export"): abort(403)
    study = FranchiseTerritory.query.filter_by(franchise_id=franchise_id).first_or_404()
    version = request.args.get("version", study.version, type=int)
    snapshot = TerritoryValuationSnapshot.query.filter_by(territory_id=study.id, version=version).first_or_404()
    financial_access(json.loads(snapshot.payload_json))
    def safe_link(value):
        try:
            url = urlsplit(value or "")
            return value if url.scheme == "https" and url.hostname and not url.username and not url.password else "#"
        except ValueError:
            return "#"
    response = current_app.make_response(render_template("franchise/territory_report.html" if json.loads(snapshot.result_json).get("assessments") else "franchise/territory_report_legacy.html", franchise=franchise,
        snapshot=snapshot, payload=json.loads(snapshot.payload_json), result=json.loads(snapshot.result_json), safe_link=safe_link,
        maps_key=current_app.config.get("GOOGLE_MAPS_API_KEY", "")))
    response.headers["Cache-Control"] = "no-store"
    return response
