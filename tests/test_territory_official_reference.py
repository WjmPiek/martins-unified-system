import json
import shutil
from pathlib import Path
import pytest
from app.franchise.territory_official_reference import ROOT,load_reference,mortality_reference
from app.franchise import territory_sources as sources
from test_territory_research import polygon


def test_verified_official_archive_covers_all_provinces_with_both_source_documents():
    data=load_reference(str(ROOT))
    assert len(data['provinces'])==9
    assert len(data['documents'])==2
    result=mortality_reference('Gauteng')
    assert abs(result['rate']-96252/15931824*1000)<0.000001
    assert result['status']=='verified_reference'
    assert result['provenance']['population_year']==result['provenance']['death_year']==2024
    assert all(len(d['sha256'])==64 and d['publication_date'] and d['table'] for d in data['documents'])


def test_official_archive_rejects_a_changed_document(tmp_path):
    shutil.copytree(ROOT,tmp_path/'archive')
    (tmp_path/'archive'/'P030932024.pdf').write_bytes(b'Changed document')
    with pytest.raises(ValueError,match='integrity'):
        load_reference(str(tmp_path/'archive'))


def test_live_failure_and_missing_verified_archive_do_not_promote_compass_data(monkeypatch):
    from app.franchise import territory_official_reference as official
    def unavailable(*a,**k):raise ValueError('Unavailable')
    monkeypatch.setattr(sources,'population_in_geometry',lambda *a:(10000,{'source':'https://doi.org/10.5258/SOTON/WP00839'}))
    monkeypatch.setattr(sources,'pdf_text',unavailable)
    monkeypatch.setattr(sources,'public_json',unavailable)
    monkeypatch.setattr(official,'mortality_reference',unavailable)
    result=sources.research(polygon(),'Gauteng','GT485',3,10)
    mortality=next(r for r in result['records'] if r['key']=='mortality_per_1000')
    assert mortality['status']=='cached_unverified'
    assert mortality['provenance'] is None


def test_population_denominator_is_matched_to_the_province_row():
    text='Appendix 1: Mid-year population estimates by province, 2024\nGauteng 15 931 824 25,3\nWestern Cape 7 562 588 12,0\n'
    assert sources.provincial_population(text,'Gauteng')==15931824
    with pytest.raises(ValueError):sources.provincial_population(text+'Gauteng 1 234 567 2,0\n','Gauteng')


def test_osm_extract_fallback_restores_roads_and_counts_only_inside_listings(monkeypatch):
    from shapely.geometry import box,mapping
    def publisher(url,params=None):
        if 'overpass' in url:raise ValueError('Temporary provider outage')
        assert url=='https://api.openstreetmap.org/api/0.6/map.json'
        return {'_source_meta':{'cached':True,'retrieved_at':'2026-10-20T10:00:00+00:00'},'elements':[
            {'type':'node','id':1,'lon':27.005,'lat':-26.005},
            {'type':'node','id':2,'lon':27.015,'lat':-26.015},
            {'type':'way','id':3,'nodes':[1,2],'tags':{'highway':'primary','name':'Test road'}},
            {'type':'node','id':4,'lon':27.01,'lat':-26.01,'tags':{'shop':'funeral_directors','name':'Inside provider'}},
            {'type':'node','id':5,'lon':27.05,'lat':-26.01,'tags':{'shop':'funeral_directors','name':'Outside provider'}}]}
    monkeypatch.setattr(sources,'public_json',publisher)
    monkeypatch.setattr(sources,'population_in_geometry',lambda *a:(10000,{'source':'https://doi.org/10.5258/SOTON/WP00839'}))
    def unavailable(*a,**k):raise ValueError('Source unavailable')
    monkeypatch.setattr(sources,'pdf_text',unavailable)
    result=sources.research(mapping(box(27,-26.02,27.02,-26)),'Gauteng','GT485',3,10)
    records={r['key']:r for r in result['records']}
    assert records['access']['value']==1 and records['access']['status']=='cached_reference'
    assert records['access']['retrieved_at']=='2026-10-20T10:00:00+00:00'
    assert records['competition']['value']==1
    assert [o['name'] for o in result['operators']]==['Inside provider']
    assert 'api.openstreetmap.org' in records['access']['sources'][0]
    assert any('bounded OpenStreetMap extract' in w for w in result['warnings'])


def test_osm_extract_fallback_does_not_silently_analyse_only_part_of_a_large_area(monkeypatch):
    calls=[]
    def unavailable(url,*a,**k):calls.append(url);raise ValueError('Unavailable')
    monkeypatch.setattr(sources,'public_json',unavailable)
    with pytest.raises(ValueError,match='small-area fallback limit'):
        sources.mapped_data('test query',[27,-27,28,-26])
    assert all('overpass' in url for url in calls)
