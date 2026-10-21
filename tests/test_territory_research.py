"""Stage 2 data provenance, geographic parity and durable research tests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import struct

import pytest
import requests
from shapely.geometry import shape, mapping, box
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from test_native_territory import app, franchise, login, payload, HEADERS
from app.extensions import db
from app.models import TerritoryResearchRun, ImportJob, Role
from app.franchise import territory_sources as sources
from app.franchise import territory_boundaries as boundaries
from app.franchise.territory_research import start, run_public_research, attach_result, fingerprint, context


def polygon(w=27.65, s=-26.34, e=27.67, n=-26.32):
    return json.loads(json.dumps(mapping(box(w, s, e, n))))


def request_data():
    return dict(geometry=polygon(), province="Gauteng", municipality_code="GT485", household_size=3,
                market_share=10, title="Westonaria", area_names=["Westonaria"])


def part(identity, geometry, selection="include"):
    return dict(id=identity, name=identity, geometry=geometry, selection=selection,
        source=boundaries.MAIN, data_date="2011-10-09", retrieved_at="2026-01-01")


def test_composition_dissolves_overlaps_and_preserves_cutouts():
    first=polygon(27,-26.03,27.02,-26.01)
    second=polygon(27.01,-26.03,27.03,-26.01)
    cut=polygon(27.012,-26.025,27.018,-26.015)
    result=shape(boundaries.compose([part("a",first),part("b",second),part("hole",cut,"exclude")]))
    expected=shape(first).union(shape(second)).difference(shape(cut))
    assert result.equals(expected)
    assert result.area < shape(first).area + shape(second).area
    with pytest.raises(ValueError): boundaries.compose([part("a",first,"exclude")])
    with pytest.raises(ValueError): boundaries.compose([part("a",first),part("a",second)])


def test_adjacent_suburbs_form_one_outline_without_shared_internal_edge():
    west=polygon(27,-26.03,27.01,-26.01)
    east=polygon(27.01,-26.03,27.02,-26.01)
    result=shape(boundaries.compose([part('west',west),part('east',east)]))
    assert result.geom_type=='Polygon'
    assert result.equals(box(27,-26.03,27.02,-26.01))
    from shapely.geometry import LineString
    internal_edge=LineString([(27.01,-26.025),(27.01,-26.015)])
    assert not result.boundary.intersects(internal_edge)


def test_census_codes_never_masquerade_as_sars_codes(monkeypatch):
    features=[{"properties":{"MP_CODE":765008,"MP_NAME":"Westonaria","PR_NAME":"Gauteng","MN_NAME":"Westonaria","MN_CODE":765},"geometry":polygon()}]
    monkeypatch.setattr(boundaries,"public_json",lambda *a,**k:{"features":features})
    a=boundaries.query_boundaries(query="Westonaria, Gauteng",kind="town")["areas"][0]
    assert a["census_municipality_code"] == "765"
    assert a["municipality_code"] == "GT485"
    assert a["data_date"] == "2011-10-09"
    features[0]["properties"]["MN_NAME"]="Unknown town municipality"
    assert boundaries.query_boundaries(query="Other",kind="town")["areas"][0]["municipality_code"] == ""


def test_official_release_uses_effective_date_not_file_modification_date(monkeypatch):
    from datetime import date
    calls=[]
    def publisher(url, params):
        calls.append((url,params))
        if '/items/' in url:
            key=next(k for k,v in boundaries.MDB_RELEASES.items() if v[0] in url)
            return dict(owner='MDBUser1',type='Feature Service',title=f'MDB Local Municipalities {key[0]}',
                url=boundaries.MDB_BASE+boundaries.MDB_RELEASES[key][1]+'/FeatureServer',modified=1789482079000)
        return {'features':[{'properties':{'CAT_B':'GT485','MUNICNAME':'Rand West City','PROVINCE':'GT'},'geometry':polygon()}]}
    monkeypatch.setattr(boundaries,'public_json',publisher)
    current=boundaries.mdb_boundaries('GT485','municipality',today=date(2026,10,20))['areas'][0]
    assert current['data_date']=='2021' and current['municipality_code']=='GT485'
    assert current['province']=='Gauteng' and not current['upcoming']
    future=boundaries.mdb_boundaries('GT485','municipality',release='2026',today=date(2026,10,20))['areas'][0]
    assert future['upcoming'] and future['effective_from']=='2026-11-04'
    active=boundaries.mdb_boundaries('GT485','municipality',today=date(2026,11,4))['areas'][0]
    assert active['data_date']=='2026' and not active['upcoming']
    assert "CAT_B = 'GT485'" in calls[-1][1]['where']
    assert current['census_municipality_code']==''


def test_official_boundary_publisher_change_fails_closed(monkeypatch):
    monkeypatch.setattr(boundaries,'public_json',lambda *a,**kw:dict(owner='other publisher'))
    with pytest.raises(ValueError,match='identity changed'):
        boundaries.query_boundaries('Rand West City')


def test_population_grid_holes_and_missing_tiles_fail_closed(tmp_path):
    manifest=dict(year=2026,encoding="gzip little-endian float32 population counts",origin=[27,-26],
        step=.005,width=2,height=2,tileSize=2,tiles=["0-0"])
    (tmp_path/"manifest.json").write_text(json.dumps(manifest))
    (tmp_path/"0-0.bin.gz").write_bytes(gzip.compress(struct.pack("<ffff",10,20,30,40)))
    entire=polygon(27,-26.01,27.01,-26)
    assert sources.population_in_geometry(entire,tmp_path)[0] == 100
    cut=shape(entire).difference(box(27,-26.005,27.005,-26))
    assert sources.population_in_geometry(json.loads(json.dumps(mapping(cut))),tmp_path)[0] == 90
    sources._tile.cache_clear()
    (tmp_path/"0-0.bin.gz").unlink()
    with pytest.raises(FileNotFoundError): sources.population_in_geometry(entire,tmp_path)


def test_sars_parser_uses_exact_code_and_rejects_changed_layout():
    text="Randfontein/Westonaria Municipality GT485 39 758  12 268  2 254  308 578  44 340  14 015  2 545  316 078\n"
    assert sources.sars_income(text,"GT485") == {"assessed_taxpayers":44340,"annual_average_taxable_income":316078}
    for code in ["GT484","765",".*"]:
        with pytest.raises(ValueError): sources.sars_income(text,code)
    with pytest.raises(ValueError): sources.sars_income(text.replace("  316 078",""),"GT485")


def test_compass_price_classification_preserves_cash_insurance_distinction():
    from app.franchise.compass_research.price_research import candidates
    cash=candidates("Cash burial funeral package R10 000 including coffin and transport.")
    insurance=candidates("Funeral insurance cover R10 000, monthly premium R99 per month.")
    assert cash[0]["price"] == 10000 and not cash[0]["excluded"]
    assert all(row["excluded"] for row in insurance)
    assert candidates("Protect your family from R165 PM. Funeral services available.")[0]["excluded"]
    assert candidates("One time fee for one listing, highlighted in funeral package search results R9.99.")[0]["excluded"]


def test_public_source_downloader_rejects_arbitrary_hosts_before_network(monkeypatch):
    called=[]
    monkeypatch.setattr(sources.requests,"get",lambda *a,**k:called.append(a))
    with pytest.raises(ValueError): sources.public_bytes("https://127.0.0.1/private")
    assert not called


def test_research_failures_are_unknown_and_cache_is_labelled(monkeypatch):
    monkeypatch.setattr(sources,"population_in_geometry",lambda *a:(10000,{"source":"https://doi.org/10.5258/SOTON/WP00839"}))
    def unavailable(*a,**k): raise requests.HTTPError("Publisher unavailable")
    monkeypatch.setattr(sources,"pdf_text",unavailable)
    monkeypatch.setattr(sources,"public_json",unavailable)
    result=sources.research(polygon(),"Gauteng","GT485",3,10)
    values={r["key"]:r for r in result["records"]}
    assert values["population"]["status"] == "cached_reference"
    assert values["mortality_per_1000"]["status"] == "verified_reference"
    assert values["competition"]["value"] is None and values["competition"]["status"] == "unavailable"
    assert values["affordability"]["value"] is None
    assert values["households"]["value"] == round(10000/2.8)
    assert "household" not in values["affordability"]["unit"]
    assert all(r["retrieved_at"] and r["confidence_reason"] for r in result["records"])


def test_research_queue_permissions_idempotence_and_snapshot_provenance(app,franchise,monkeypatch):
    client=login(app,franchise)
    url=f"/franchise/{franchise.id}/territory"
    started=client.post(url+"/research",json=request_data(),headers=HEADERS)
    assert started.status_code == 202
    run=db.session.get(TerritoryResearchRun,started.json["run_id"])
    assert client.post(url+"/research",json=request_data(),headers=HEADERS).json["run_id"] == run.id
    job=db.session.get(ImportJob,run.job_id)
    called=[]
    fake={"records":[],"operators":[],"roads":[],"public_prices":[],"proposed_inputs":{"population":10000},
          "warnings":[],"completed_at":"2026-01-01T00:00:00+00:00","method_version":"test"}
    def research(**kwargs):called.append(kwargs);return fake
    monkeypatch.setattr(sources,"research",research)
    run_public_research(job)
    run_public_research(job)
    assert len(called)==1
    read=client.get(url+f"/research/{run.id}")
    assert read.json["status"] == "completed"
    data=payload();data.update(geometry=request_data()["geometry"],research_run_id=run.id)
    response=client.post(url+"/save",json={"version":0,"payload":data},headers=HEADERS)
    assert response.status_code==200
    exported=client.get(url+"/export").json
    assert exported["result"]["research"]["run_id"] == run.id
    report=client.get(url+"/report")
    assert report.status_code==200 and b"Where the information was found" in report.data
    assert b"Revision 1" in report.data
    data["geometry"]=polygon(27.66,-26.35,27.67,-26.33)
    assert client.post(url+"/calculate",json=data,headers=HEADERS).status_code==400
    data["geometry"]=request_data()["geometry"];data["inputs"]["market_share"]=15
    assert client.post(url+"/calculate",json=data,headers=HEADERS).status_code==400


def test_worker_revocation_prevents_external_research(app,franchise,monkeypatch):
    client=login(app,franchise)
    run=start(franchise.id,1,request_data())
    job=db.session.get(ImportJob,run.job_id)
    Role.query.filter_by(name="Admin").one().permissions=[];db.session.commit()
    called=[]
    monkeypatch.setattr(sources,"research",lambda **kwargs:called.append(kwargs))
    with pytest.raises(ValueError,match="revoked"):run_public_research(job)
    assert run.status=="cancelled" and not called


def test_cash_packages_feed_funeral_services_and_keep_source_in_report(app,franchise):
    client=login(app,franchise)
    raw=context(request_data())
    result={"records":[],"operators":[],"roads":[],"warnings":[],"proposed_inputs":{},
        "completed_at":"2026-01-01T00:00:00+00:00","method_version":"test",
        "public_prices":[{"business":"Test funeral provider","url":"https://example.com/packages", "checkedAt":"2026-01-01T00:00:00+00:00", "scope":"Advertised cash packages", "materials":[],
            "candidates":[{"price":10000,"kind":"Cash package candidate — confirm inclusions","excluded":False,"serviceType":"burial","source":"https://example.com/cash-package","context":"Cash funeral service package"},
                          {"price":165,"kind":"Excluded: monthly premium","excluded":True,"serviceType":"burial"}]}]}
    run=TerritoryResearchRun(franchise_id=franchise.id,created_by_id=1,input_hash=fingerprint(raw),request_json=json.dumps(raw),result_json=json.dumps(result),status="completed",finished_at=datetime.now(timezone.utc))
    db.session.add(run);db.session.commit()
    data=payload();data.update(geometry=raw["geometry"],research_run_id=run.id,
        service_price_selections={"burial":{"run_id":run.id,"source_index":0,"candidate_index":0}})
    data["inputs"]["burial_price"]=10000
    url=f"/franchise/{franchise.id}/territory"
    calculation=client.post(url+"/calculate",json=data,headers=HEADERS)
    assert calculation.status_code==200
    # Demand now always follows the selected geometry; isolate the cash price
    # effect by comparing the same territory with the original R20,000 price.
    comparison=deepcopy(data);comparison['inputs']['burial_price']=20000
    doubled=client.post(url+'/calculate',json=comparison,headers=HEADERS)
    from decimal import Decimal
    assert abs(Decimal(doubled.json['expected']['months'][0]['revenue'])-2*Decimal(calculation.json['expected']['months'][0]['revenue']))<=Decimal('.01')
    assert calculation.json["selected_service_prices"]["burial"]["used_in_forecast"]
    assert client.post(url+"/save",json={"version":0,"payload":data},headers=HEADERS).status_code==200
    report=client.get(url+"/report").get_data(as_text=True)
    assert "Cash packages included in funeral services" in report
    assert "https://example.com/cash-package" in report
    assert "Used as the service price in this forecast" in report
    data["service_price_selections"]["burial"]["candidate_index"]=1
    assert client.post(url+"/calculate",json=data,headers=HEADERS).status_code==400
    result['public_prices'][0]['candidates'][0]['context']='One time fee for one listing R9.99; edit packages in the directory'
    run.result_json=json.dumps(result);db.session.commit()
    data['service_price_selections']['burial']['candidate_index']=0
    assert client.post(url+'/calculate',json=data,headers=HEADERS).status_code==400


def test_research_cross_franchise_and_missing_csrf(app,franchise):
    client=login(app,franchise,role="Franchise User")
    base=f"/franchise/{franchise.id}/territory"
    assert client.post(base+"/research",json=request_data(),headers=HEADERS).status_code==403
    assert client.get("/franchise/999/territory/research/1").status_code==403
    assert client.get("/franchise/999/territory/report").status_code==403


def test_existing_google_maps_configuration_used_by_page_and_report(app,franchise):
    client=login(app,franchise)
    app.config['GOOGLE_MAPS_API_KEY']='configured-browser-test-key'
    base=f'/franchise/{franchise.id}/territory'
    page=client.get(base).get_data(as_text=True)
    assert 'key=configured-browser-test-key' in page and 'callback=initTerritoryMap' in page
    data=payload();data['geometry']=polygon()
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    report=client.get(base+'/report').get_data(as_text=True)
    assert 'key=configured-browser-test-key' in report and 'callback=initTerritoryReportMap' in report
    assert 'window.gm_authFailure' in report


def test_stage2_migration_preserves_existing_studies(app,franchise):
    TerritoryResearchRun.__table__.drop(db.engine)
    path=Path(__file__).parents[1]/"migrations/versions/v129_territory_research.py"
    spec=importlib.util.spec_from_file_location("research_migration",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with db.engine.begin() as connection:
        module.op=Operations(MigrationContext.configure(connection));module.upgrade()
        assert "territory_research_runs" in inspect(connection).get_table_names()
        assert connection.exec_driver_sql("SELECT count(*) FROM franchises").scalar()==1
        module.downgrade()
        assert "franchise_territories" in inspect(connection).get_table_names()
        module.upgrade()


def test_research_cannot_be_reused_for_another_province_and_legacy_run_still_attaches(app,franchise):
    import hashlib
    client=login(app,franchise)
    raw=context(request_data())
    legacy_fields={key:raw[key] for key in ('geometry','province','municipality_code','market_share')}
    legacy_hash=hashlib.sha256(json.dumps(legacy_fields,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    run=TerritoryResearchRun(franchise_id=franchise.id,created_by_id=1,input_hash=legacy_hash,
        request_json=json.dumps(raw),result_json=json.dumps({'records':[],'proposed_inputs':{}}),
        status='completed',finished_at=datetime.now(timezone.utc))
    db.session.add(run);db.session.commit()
    data=payload();data.update(geometry=raw['geometry'],research_run_id=run.id,area_province='Gauteng')
    base=f'/franchise/{franchise.id}/territory'
    assert client.post(base+'/calculate',json=data,headers=HEADERS).status_code==200
    data['area_province']='Western Cape'
    response=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert response.status_code==400 and 'province' in response.json['error']
    changed={**raw,'title':'Another place'}
    assert fingerprint(raw)!=fingerprint(changed)


def test_durable_queue_worker_completes_research_and_retry_status_remains_visible(app,franchise,monkeypatch):
    from app.jobs import run_next_job
    from app.models import WorkerHeartbeat
    client=login(app,franchise)
    base=f'/franchise/{franchise.id}/territory'
    started=client.post(base+'/research',json=request_data(),headers=HEADERS)
    run=db.session.get(TerritoryResearchRun,started.json['run_id'])
    state=client.get(base+f'/research/{run.id}').json
    assert not state['worker_available'] and 'No active' in state['message']
    fake={'records':[],'proposed_inputs':{},'warnings':[],'completed_at':'2026-10-20','method_version':'queue-test'}
    monkeypatch.setattr(sources,'research',lambda **kwargs:fake)
    job=run_next_job(queue_name='territory',worker_id='territory-test-worker')
    assert job.status=='completed' and run.status=='completed'
    state=client.get(base+f'/research/{run.id}').json
    assert state['status']=='completed' and state['worker_available']
    assert state['result']['method_version']=='queue-test'
    # A retry queued by the shared job system must not look permanently failed.
    run.status='failed';job.status='queued';job.message='Retry scheduled after failure'
    db.session.commit()
    state=client.get(base+f'/research/{run.id}').json
    assert state['status']=='queued' and 'Retry scheduled' in state['message']


def test_mortality_parser_uses_residence_count_in_the_published_four_column_table():
    # Stats SA P0309.3 2024 table 3.6, PDF page 29 (printed page 23).
    text='Table 3.6 - Distribution of deaths by province of death occurrence and province of usual residence\n'
    text+='Gauteng 100 329 21,2 96 252 20,3\nKwa-Zulu Natal 89 002 18,8 88 827 18,7\n'
    assert sources.usual_residence_deaths(text,'Gauteng')==96252
    assert sources.usual_residence_deaths(text,'KwaZulu-Natal')==88827
    with pytest.raises(ValueError):sources.usual_residence_deaths(text.replace('96 252','Unknown'),'Gauteng')
    with pytest.raises(ValueError):sources.usual_residence_deaths(text+'Gauteng 100 329 21,2 95 000 20,0\n','Gauteng')


def test_competitor_attribution_excludes_holes_nearby_own_network_and_duplicates():
    from shapely.geometry import Polygon
    from app.franchise.territory_sources import classify_operators
    polygon=Polygon([(0,0),(4,0),(4,4),(0,4)],holes=[[(1,1),(2,1),(2,2),(1,2)]])
    rows=[dict(name='Independent',lat=3,lon=3,source='https://a.test/branch'),
        dict(name='Independent',latitude=3,longitude=3,source='https://b.test/branch'),
        dict(name='Hole operator',latitude=1.5,longitude=1.5,source='https://c.test'),
        dict(name='Nearby',lat=5,lon=5,source='https://d.test'),
        dict(name='Unknown',source='https://e.test'),
        dict(name='Martins Funerals Westonaria',lat=3,lon=2,source='https://martins.test'),
        dict(name='Invalid coordinates',lat=float('nan'),lon=3,source='https://f.test')]
    operators,summary=classify_operators(rows,polygon)
    assert summary==dict(inside_external=1,nearby=2,unlocated=2,martins_network=1,total_leads=6)
    assert operators[0]['additional_sources']==['https://b.test/branch']
    assert operators[1]['territory_relation']=='outside / nearby'
    assert not operators[4]['counted_inside']
    assert 'territory_relation' not in rows[0]


def test_territory_worker_queue_and_stale_recovery_leave_other_jobs_untouched(app,franchise):
    from datetime import datetime,timedelta,timezone
    from app.jobs import enqueue_job,release_stale_jobs,register_worker_heartbeat
    from app.models import ImportJob
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    started=client.post(base+'/research',json=request_data(),headers=HEADERS)
    assert started.status_code==202
    run=db.session.get(TerritoryResearchRun,started.json['run_id'])
    research_job=db.session.get(ImportJob,run.job_id)
    assert research_job.queue_name=='territory'
    other=enqueue_job('unrelated-test',filename='Existing work',queue_name='default')
    for job in [research_job,other]:
        job.status='running';job.heartbeat_at=datetime.now(timezone.utc)-timedelta(hours=1)
    db.session.commit()
    assert release_stale_jobs(queue_name='territory')==1
    assert research_job.status=='queued' and other.status=='running'
    register_worker_heartbeat('territory-polling-test',queue_name='territory',status='polling')
    assert client.get(base+f'/research/{run.id}').json['worker_available']
