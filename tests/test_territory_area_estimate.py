import gzip
import json
import struct
from decimal import Decimal
from copy import deepcopy
from test_native_territory import app,franchise,login,payload,HEADERS
from test_territory_research import polygon,part
from app.franchise import territory_sources as sources
from app.franchise import territory_area_estimate as estimates
from app.franchise import territory_boundaries as boundaries


def test_including_and_removing_areas_updates_figures_without_double_counting(app,franchise,tmp_path,monkeypatch):
    manifest=dict(year=2026,encoding='gzip little-endian float32 population counts',origin=[27,-26],step=.005,
        width=2,height=2,tileSize=2,tiles=['0-0'],source='https://doi.org/10.5258/SOTON/WP00839')
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    (tmp_path/'0-0.bin.gz').write_bytes(gzip.compress(struct.pack('<ffff',10000,20000,0,0)))
    monkeypatch.setattr(estimates,'population_in_geometry',lambda g:sources.population_in_geometry(g,tmp_path))
    client=login(app,franchise)
    base=f'/franchise/{franchise.id}/territory'
    data=payload();data.update(scores={},score_evidence={});data['inputs'].update(expense_mode='percentage',expense_percent=70,population=999999)
    west=polygon(27,-26.005,27.005,-26);east=polygon(27.005,-26.005,27.01,-26)
    data.update(area_linked_figures=False,area_province='Gauteng',geometry=west)
    small=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert small.status_code==200
    assert small.json['calculated_inputs']['population']==10000
    assert small.json['calculated_scores']['population']==2
    assert all(small.json['calculated_score_evidence'].values())
    assert len(small.json['calculated_score_evidence'])==7
    assert 'No verified source for a numeric score' in small.json['calculated_score_evidence']['access']
    # Legacy opt-out flags cannot disable recalculation for a selected boundary.
    page=client.get(base).get_data(as_text=True)
    assert 'id="area-linked-figures"' not in page
    assert 'recalculate automatically' in page
    parts=[part('west',west),part('east',east)]
    data['geometry']=boundaries.compose(parts);data['boundary_parts']=parts
    large=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert large.status_code==200 and large.json['calculated_inputs']['population']==30000
    for key in ['services','revenue','royalty','overhead','operating']:
        assert abs(Decimal(large.json['expected']['months'][0][key])-3*Decimal(small.json['expected']['months'][0][key]))<=Decimal('.02')
    assert large.json['area_estimate']['estimated_annual_deaths']>small.json['area_estimate']['estimated_annual_deaths']
    assert large.json['area_estimate']['mortality_verified']
    assert large.json['recommended_fee'] is None
    # Overlapping polygons are unioned before population and revenue are estimated.
    parts.append(part('duplicate-footprint',west));data['boundary_parts']=parts;data['geometry']=boundaries.compose(parts)
    overlapping=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert overlapping.json['calculated_inputs']['population']==30000
    assert overlapping.json['expected']['months'][0]['revenue']==large.json['expected']['months'][0]['revenue']
    # Removing the eastern area reduces the figures to the original western area.
    parts[1]['selection']='exclude';data['boundary_parts']=parts;data['geometry']=boundaries.compose(parts)
    smaller=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert smaller.json['calculated_inputs']['population']==10000
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    snapshot=client.get(base+'/export').json
    assert snapshot['payload']['inputs']['population']==10000
    assert snapshot['payload']['scores']['population']==2
    assert snapshot['payload']['score_evidence']['population'].startswith('Automatic suggestion:')
    assert len(snapshot['payload']['score_evidence'])==7
    assert snapshot['result']['recommended_fee'] is None
    report=client.get(base+'/report').get_data(as_text=True)
    assert 'Figures linked to the selected territory' in report and 'Original source' in report


def test_suburb_point_lookup_and_neighbour_descriptions_are_sourced(monkeypatch):
    calls=[]
    def publisher(url,params):
        calls.append(params)
        return {'features':[{'properties':{'SP_CODE':765008001,'SP_NAME':'Westonaria SP1','MP_CODE':765008,'MP_NAME':'Westonaria','MN_CODE':765,'MN_NAME':'Westonaria','PR_NAME':'Gauteng'},'geometry':polygon()}]}
    monkeypatch.setattr(boundaries,'public_json',publisher)
    result=boundaries.query_boundaries(kind='suburb',point=[27.66,-26.33])
    assert calls[0]['geometryType']=='esriGeometryPoint'
    assert calls[0]['spatialRel']=='esriSpatialRelIntersects'
    area=result['areas'][0]
    assert area['description']=='Westonaria SP1 · Westonaria · Westonaria · Gauteng · 2011 census boundary'
    assert len(area['label_point'])==2 and area['data_date']=='2011-10-09'
    import pytest
    for point in [[True,-26.33],[27,float('nan')],[0,0],['27',-26.33]]:
        with pytest.raises(ValueError):boundaries.query_boundaries(kind='suburb',point=point)


def test_whole_boundary_includes_detached_areas_subtracts_holes_and_partial_cells(tmp_path):
    from shapely.geometry import box, mapping
    from shapely.ops import unary_union
    manifest=dict(year=2026,encoding='gzip little-endian float32 population counts',origin=[27,-26],step=.005,
        width=4,height=2,tileSize=4,tiles=['0-0'],source='https://doi.org/10.5258/SOTON/WP00839')
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    # A high-population unselected cell between selected areas must never count.
    (tmp_path/'0-0.bin.gz').write_bytes(gzip.compress(struct.pack('<'+'f'*16,10000,900000,20000,*([0]*13))))
    west=box(27,-26.005,27.005,-26)
    hole=box(27.001,-26.004,27.002,-26.001)  # 12% of the western cell
    east=box(27.01,-26.005,27.015,-26)
    geometry=json.loads(json.dumps(mapping(unary_union([west.difference(hole),east]))))
    count,info=sources.population_in_geometry(geometry,tmp_path)
    assert count==28800  # 8,800 west + 20,000 detached east; neither gap nor hole
    assert info['analysis']['coverage']=='whole_selected_boundary'
    assert info['analysis']['polygon_count']==2
    assert info['analysis']['hole_count']==1
    assert len(info['analysis']['boundary_sha256'])==64
    # A sliver between the former sampling points must still contribute.
    narrow=json.loads(json.dumps(mapping(box(27,-26.005,27.0001,-26))))
    assert sources.population_in_geometry(narrow,tmp_path)[0]==200
    import pytest
    beyond=json.loads(json.dumps(mapping(box(26.999,-26.005,27.005,-26))))
    with pytest.raises(ValueError,match='whole selected boundary'):
        sources.population_in_geometry(beyond,tmp_path)


def test_enclosed_interiors_feed_population_demand_and_income_but_explicit_cutouts_do_not(app,franchise,tmp_path,monkeypatch):
    from shapely.geometry import box,mapping,shape
    manifest=dict(year=2026,encoding='gzip little-endian float32 population counts',origin=[27,-26],step=.005,
        width=3,height=3,tileSize=3,tiles=['0-0'],source='https://doi.org/10.5258/SOTON/WP00839')
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    (tmp_path/'0-0.bin.gz').write_bytes(gzip.compress(struct.pack('<fffffffff',*[1000]*9)))
    monkeypatch.setattr(estimates,'population_in_geometry',lambda g:sources.population_in_geometry(g,tmp_path))
    outer=box(27,-26.015,27.015,-26);centre=box(27.005,-26.01,27.01,-26.005)
    parts=[part('surrounding-suburbs',json.loads(json.dumps(mapping(outer.difference(centre)))))]
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    data=payload();data.update(boundary_parts=parts,area_province='Gauteng',include_enclosed=False,geometry=boundaries.compose(parts))
    legacy_response=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert legacy_response.status_code==200,legacy_response.json
    legacy=legacy_response.json
    assert legacy['calculated_inputs']['population']==8000
    data.update(include_enclosed=True,geometry=boundaries.compose(parts,include_enclosed=True))
    assert shape(data['geometry']).equals(outer)
    filled=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert filled.status_code==200 and filled.json['calculated_inputs']['population']==9000
    assert filled.json['area_estimate']['estimated_annual_deaths']>legacy['area_estimate']['estimated_annual_deaths']
    assert Decimal(filled.json['expected']['months'][0]['revenue'])>Decimal(legacy['expected']['months'][0]['revenue'])
    parts.append(part('explicit-enclave-cutout',json.loads(json.dumps(mapping(centre))),'exclude'))
    data.update(boundary_parts=parts,geometry=boundaries.compose(parts,include_enclosed=True))
    cut=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert cut.status_code==200 and cut.json['calculated_inputs']['population']==8000
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    assert client.get(base+'/export').json['payload']['include_enclosed'] is True
    assert 'Enclosed interiors are included automatically' in client.get(base+'/report').get_data(as_text=True)
