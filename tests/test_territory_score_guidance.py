from copy import deepcopy
from test_native_territory import app,franchise,login,payload,HEADERS
from app.models import FranchiseTerritory
from app.franchise.territory_finance import WEIGHTS
from app.franchise.territory_score_guidance import recommendations


def record(key,value,confidence='medium',status='available'):
    return dict(key=key,value=value,confidence=confidence,status=status,label=key,unit='test units',
        data_period='2026',retrieved_at='2026-10-20T10:00:00+00:00',sources=['https://example.com/source'])


def test_all_seven_factors_have_explicit_neutral_assumptions_when_evidence_missing():
    original=payload();before=deepcopy(original)
    result=recommendations(original)
    assert set(result['entries'])==set(WEIGHTS)
    assert all(e['action'] and e['rubric'] and e['reason'] and e['recommended_score']==3 and e['status']=='planning_assumption' for e in result['entries'].values())
    assert original==before
    assert 'not approved Martins standards' in result['note']


def test_provisional_bands_require_supported_evidence_and_keep_source_scope():
    research=dict(records=[record('population',17721),record('capturable_services',0.89,'low'),
        record('competition',0,'low'),record('affordability',316078,'low'),record('growth',1.9,'low')])
    result=recommendations(payload(),research)['entries']
    assert result['population']['recommended_score']==2 and result['population']['confidence']=='low'
    assert result['population']['sources']==['https://example.com/source']
    for key in ['demand','competition','affordability','growth','access','policy']:
        assert result[key]['recommended_score']==3 and result[key]['status']=='planning_assumption'
    assert 'taxpayer context' in result['affordability']['reason']
    research['stale']=True
    assert recommendations(payload(),research)['entries']['population']['status']=='planning_assumption'


def test_recorded_franchise_activity_does_not_claim_new_territory_population_demand():
    data=payload();data['inputs']['demand_basis']='franchise_history'
    actuals=dict(proposed_inputs={'recorded_monthly_services':10},window_start='2025-10',window_end='2026-09',checked_at='2026-10-20',observed_months=12)
    item=recommendations(data,actuals=actuals)['entries']['demand']
    assert item['recommended_score']==4 and item['confidence']=='low'
    assert any('Confirm that these services represent the selected territory' in text for text in item['context'])


def test_guidance_endpoint_is_scoped_and_never_saves_or_accepts_scores(app,franchise):
    client=login(app,franchise)
    base=f'/franchise/{franchise.id}/territory'
    response=client.post(base+'/score-recommendations',json=payload(),headers=HEADERS)
    assert response.status_code==200
    assert set(response.json['entries'])==set(WEIGHTS)
    assert FranchiseTerritory.query.count()==0
    assert client.post('/franchise/999/territory/score-recommendations',json=payload(),headers=HEADERS).status_code==404
    assert client.post(base+'/score-recommendations',json=payload()).status_code==400
    page=client.get(base).get_data(as_text=True)
    assert all(label in page for label in ['Population and households','Annual funeral demand','Affordability and service mix','Competition','Policy sales opportunity','Accessibility','Growth potential'])


def test_automatic_evidence_refresh_preserves_manual_score_review():
    from app.franchise.territory_score_guidance import populate_suggestions
    data=payload();data.update(scores={},score_evidence={})
    guidance=recommendations(data,dict(records=[record('population',17721)]))
    populate_suggestions(data,guidance)
    assert data['scores']=={k:(2 if k=='population' else 3) for k in WEIGHTS}
    assert len(data['score_evidence'])==7
    assert 'https://example.com/source' in data['score_evidence']['population']
    assert '2026' in data['score_evidence']['population']
    assert data['score_evidence']['competition'].startswith('Automatic suggestion: score=3;')
    assert 'not a researched finding' in data['score_evidence']['competition']
    guidance=recommendations(data,dict(records=[record('population',60000)]))
    populate_suggestions(data,guidance)
    assert data['scores']['population']==4
    data['scores']['population']=3
    data['score_evidence']['population']='Owner reviewed local capacity and approved 3/5'
    populate_suggestions(data,guidance)
    assert data['scores']['population']==3
    assert data['score_evidence']['population']=='Owner reviewed local capacity and approved 3/5'


def test_policy_evidence_uses_approved_cash_flow_without_inventing_sales():
    actuals=dict(totals={'insurance_receipts':'60000','insurance_payover':'54000'}, observed_months=3,
        coverage_months=12, window_start='2025-10', window_end='2026-09', checked_at='2026-10-20',
        franchise_id=7, source_rows=[{'id':81},{'id':82},{'id':83}], confidence='medium')
    item=recommendations(payload(),actuals=actuals)['entries']['policy']
    evidence=' '.join(item['context'])
    assert 'R20,000.00' in evidence and 'R18,000.00' in evidence and 'R2,000.00' in evidence
    assert '3 recorded months out of 12' in evidence and '81, 82, 83' in evidence
    assert item['recommended_score']==3 and item['status']=='planning_assumption' and item['confidence']=='unverified'
    assert item['source_checked_at']=='2026-10-20' and 'not a count of policies' in evidence
    assert recommendations(payload())['entries']['policy']['context']==[]


def test_populated_assumptions_do_not_unlock_a_recommended_price(app,franchise):
    client=login(app,franchise)
    data=payload();data.update(scores={},score_evidence={})
    response=client.post(f'/franchise/{franchise.id}/territory/calculate',json=data,headers=HEADERS)
    assert response.status_code==200
    result=response.json
    assert set(result['calculated_scores'])==set(WEIGHTS)
    assert set(result['assumed_score_factors'])==set(WEIGHTS)
    assert result['recommended_fee'] is None and not result['evidence_complete']
    assert 'Neutral 3/5 assumptions' in result['recommendation_reason']


def test_new_editor_and_saved_print_report_populate_every_factor(app,franchise):
    import re
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    page=client.get(base).get_data(as_text=True)
    assert len(re.findall(r'<textarea[^>]*data-score-evidence="[^"]+"[^>]*>Automatic suggestion: score=3;',page))==7
    data=payload();data.update(scores={},score_evidence={})
    saved=client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS)
    assert saved.status_code==200
    exported=client.get(base+'/export').json
    assert len(exported['payload']['scores'])==7
    report=client.get(base+'/report').get_data(as_text=True)
    for key,next_key in [('commercial','running'),('prospective',None)]:
        panel=report.split('id="assessment-panel-'+key+'"',1)[1]
        if next_key:panel=panel.split('id="assessment-panel-'+next_key+'"',1)[0]
        assert panel.count('/5 (neutral planning assumption)')==7
    assert 'Neutral 3/5 assumptions' in report
