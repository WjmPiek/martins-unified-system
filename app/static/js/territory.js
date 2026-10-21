(() => {
  'use strict';
  const ctx = JSON.parse(document.getElementById('territory-context').textContent);
  const byId = id => document.getElementById(id);
  // The fresh-assessment flag is a one-time action, not a saved report mode.
  if(window.history?.replaceState){
    const address=new URL(window.location.href);
    if(address.searchParams.get('scenario')==='prospective'){address.searchParams.delete('scenario');window.history.replaceState(null,'',address);}
  }
  const status = text => { byId('territory-status').textContent = text; };
  const decimal = (value,places=2) => new Intl.NumberFormat('en-GB', {minimumFractionDigits:places,maximumFractionDigits:places}).format(Number(value));
  const rand = value => 'R ' + decimal(value);
  function dateText(value){
    const text=String(value??'');
    if(!/^\d{4}-\d{2}(?:-\d{2}(?:[T ].*)?)?$/.test(text))return text;
    const monthOnly=text.length===7,dateOnly=text.length===10;
    const date=new Date(monthOnly?text+'-01T00:00:00Z':dateOnly?text+'T00:00:00Z':text);
    if(!Number.isFinite(date.getTime()))return text;
    return new Intl.DateTimeFormat('en-GB',{timeZone:'Africa/Johannesburg',year:'numeric',month:'long',
      ...(!monthOnly?{day:'2-digit'}:{}),...(!monthOnly&&!dateOnly?{hour:'2-digit',minute:'2-digit',hour12:false}:{})}).format(date)+(!monthOnly&&!dateOnly?' SAST':'');
  }
  let map;
  let namedSearchPending=false,namedSearchGeneration=0;
  let googleUnavailable = false;
  let version = ctx.version;
  let busy = false,saving=false,researchPending=false;
  let pendingAutoForecast=false,inputRevision=0;
  let areas = ctx.payload.boundary_parts || [];
  let includeEnclosed=ctx.payload.include_enclosed===true;
  let appliedSelections=new Map(areas.map(a=>[a.id,a.selection]));
  let researchRunId = ctx.payload.research_run_id || null;
  let researchResult = ctx.savedResult?.research || null;
  let servicePriceSelections = ctx.payload.service_price_selections || {};
  let financialBaseline = ctx.payload.financial_baseline || null;
  let scenarioMode=ctx.payload.scenario_mode||'existing',comparisonBaseline=ctx.payload.comparison_baseline||null;
  let scoreRecommendations = null;
  let calculatedProfitFee = null,calculatedCommercialFee=null;
  let researchGeneration = 0;
  let leafMap, leafParts, leafTerritory;
  byId('geometry').value = ctx.payload.geometry ? JSON.stringify(ctx.payload.geometry, null, 2) : '';
  byId('sources').value = JSON.stringify(ctx.payload.sources || [], null, 2);
  function geometry() {
    const value = byId('geometry').value.trim();
    if (!value) return null;
    const parsed = JSON.parse(value);
    return parsed.type === 'Feature' ? parsed.geometry : parsed;
  }
  function collect() {
    const inputs = {salary_in_fixed:false};
    document.querySelectorAll('[data-input]').forEach(el => {
      inputs[el.dataset.input] = el.type === 'checkbox' ? el.checked : el.dataset.format ? fieldNumber(el) : el.type === 'number' ? (el.value.trim() === '' ? null : Number(el.value)) : el.value;
    });
    const scores = {}, evidence = {};
    document.querySelectorAll('[data-score]').forEach(el => { if (el.value !== '' && Number.isFinite(Number(el.value))) scores[el.dataset.score] = Number(el.value); });
    document.querySelectorAll('[data-score-evidence]').forEach(el => { evidence[el.dataset.scoreEvidence] = el.value; });
    return { inputs, scores, score_evidence: evidence, geometry: geometry(), sources: JSON.parse(byId('sources').value), boundary_note: byId('boundary-note').value,
      boundary_parts: areas.filter(a => a.selection !== 'context'), research_run_id: researchRunId, service_price_selections: servicePriceSelections, financial_baseline:scenarioMode==='prospective'?null:financialBaseline, comparison_baseline:comparisonBaseline, scenario_mode:scenarioMode,
      ...(scenarioMode==='prospective'?{prospect:{name:byId('prospect-name').value,royalty_percent:fieldNumber(byId('prospect-royalty'))}}:{}), area_linked_figures:true, include_enclosed:includeEnclosed, area_province:byId('research-province').value };
  }
  function fieldNumber(el){
    let text=String(el.value).trim();
    if(!text)return null;
    if(el.dataset.format==='currency')text=text.replace(/^R\s*/i,'');
    if(el.dataset.format==='percentage')text=text.replace(/%$/,'');
    text=text.replace(/[\s\u00a0\u202f]/g,'');
    // Accept displayed comma groups and ungrouped decimal-comma entries.
    if(/^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$/.test(text))text=text.replace(/,/g,'');
    else if(/^[+-]?\d+,\d{1,2}$/.test(text))text=text.replace(',','.');
    if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)||!Number.isFinite(Number(text)))
      throw new Error('Enter a valid number for '+(el.dataset.label||el.dataset.input)+'.');
    const value=Number(text);
    if(el.dataset.format==='integer'&&!Number.isInteger(value))throw new Error('Enter a whole number for '+(el.dataset.label||el.dataset.input)+'.');
    return value;
  }
  function setFieldValue(el,value){
    if(!el)return;
    if(value===''||value===null||!el.dataset.format||!Number.isFinite(Number(value))){el.value=value??'';return;}
    const kind=el.dataset.format;
    el.value=kind==='currency'?rand(value):kind==='percentage'?decimal(value)+'%':
      el.dataset.input==='start_year'?String(Number(value)):kind==='integer'?decimal(value,Number.isInteger(Number(value))?0:2):
      decimal(value,el.dataset.input==='mortality_per_1000'?6:2);
  }
  document.querySelectorAll('[data-format]').forEach(el=>{
    setFieldValue(el,el.value);
    el.addEventListener('focus',()=>{if(el.readOnly)return;try{const value=fieldNumber(el);el.value=value===null?'':String(value);}catch(_){/* Keep invalid text available for correction. */}});
    el.addEventListener('blur',()=>{try{const value=fieldNumber(el);setFieldValue(el,value);el.setCustomValidity('');}catch(error){el.setCustomValidity(error.message);status(error.message);}});
    el.addEventListener('input',()=>el.setCustomValidity(''));
  });
  function renderSources(sources) {
    const list = byId('source-list'); list.replaceChildren();
    sources.forEach(source => {
      const p = document.createElement('p');
      p.textContent = `${source.metric}: ${source.value} · Data ${dateText(source.data_date)} · Retrieved ${dateText(source.retrieved_at)} · ${source.confidence} confidence · ${source.method} `;
      const link = document.createElement('a');
      try { const url = new URL(source.url); if (url.protocol === 'https:') { link.href = url.href; link.textContent = 'Source'; link.target = '_blank'; link.rel = 'noopener'; p.append(link); } } catch (_) { /* Invalid links remain plain text until validated. */ }
      list.append(p);
    });
  }
  function renderScenario(){
    const prospective=scenarioMode==='prospective';byId('prospect-controls').hidden=!prospective;
    byId('prospective-fee-heading').textContent='New prospective franchise — suggested territory fee';
    byId('scenario-mode-status').textContent=prospective?'Prospective area-only forecast: no holder income, service prices, recorded activity, policy book or agreement terms are used.':'Existing-business forecast. Holder figures are applied only when explicitly loaded.';
    byId('input-recorded_monthly_services').readOnly=prospective;
    if(prospective)byId('territory-scenario-heading').textContent='Prospective territory: '+byId('prospect-name').value+' · Revision '+version+' · Draft for review';
    else if(ctx.holderName)byId('territory-scenario-heading').textContent='Franchise holder: '+ctx.holderName+' · Revision '+version+' · Draft for review';
  }
  function renderRunningConcern(data){
    const list=byId('running-concern-figures');list.replaceChildren();
    byId('running-concern-fee').textContent=data?.valuation?.fee?.fair_fee==null?'Unavailable':rand(data.valuation.fee.fair_fee);
    byId('running-concern-assumptions').textContent=data?.valuation_note||'Load approved holder figures to calculate the separate running-concern territory fee.';
    if(!data){byId('running-concern-status').textContent='No running-concern comparison loaded. Area-only estimates remain available.';return;}
    byId('running-concern-status').textContent=`${data.business_name||'Existing business'} · ${data.observed_months}/12 approved months · ${data.confidence} record-coverage confidence. Sources and assumptions appear in the selected details.`;
    [['Monthly business income',data.monthly_business_income==null?'Not recorded':rand(data.monthly_business_income)],
      ['Recorded funerals per month',data.monthly_services==null?'Not recorded':decimal(data.monthly_services)],
      ['Recorded monthly royalties',data.monthly_royalties==null?'Not recorded':rand(data.monthly_royalties)],['Actual operating profit','Not recorded']].forEach(([label,value])=>{
      const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;row.append(dt,dd);list.append(row);
    });
  }
  function renderProspectAssumptions(data){
    byId('prospective-assumptions').hidden=!data;
    if(!data)return;
    byId('prospect-assumptions-note').textContent=data.note;
    const list=byId('prospect-assumptions-list');list.replaceChildren();
    data.rows.forEach(item=>{const row=document.createElement('p');row.textContent=`${item.label}: ${item.value} · ${item.status.replaceAll('_',' ')} · ${item.confidence} confidence · As at ${dateText(item.checked_at)}. ${item.basis} `;if(item.source)sourceLink(row,item.source,'Source');list.append(row);});
  }
  function render(result) {
    renderScenario();renderRunningConcern(result.running_concern||null);renderProspectAssumptions(result.prospective||null);
    if(result.commercial_fee){
      const fee=result.commercial_fee;calculatedCommercialFee=fee.amount;
      byId('commercial-fee-amount').textContent=rand(fee.amount);
      byId('commercial-fee-checks').textContent=fee.affordable?'Within the affordable ceiling under the entered assumptions.':'Review affordability before offering this commercial fee.';
      byId('commercial-fee-comparison').textContent=fee.comparison==='unavailable'?'Correct the assumptions before comparing the two amounts.':fee.comparison==='equal'?'Our commercial fee equals the optional fairness amount.':`Our commercial fee is ${rand(fee.difference)} ${fee.comparison} the optional fairness amount.`;
      const apply=byId('apply-commercial-fee');if(apply)apply.disabled=!ctx.editable;
    }

    if(result.profit_margin_fee){
      const fee=result.profit_margin_fee;calculatedProfitFee=fee.fair_method_version?fee.fair_fee:null;
      byId('profit-margin-fee-status').textContent=fee.selected?'Optional Territory Fee selected. Apply the capped amount below to test its repayments.':'Comparison only. The selected purchase-fee method is unchanged.';
      byId('profit-margin-fee-years').replaceChildren();
      Object.entries({turnover:'Turnover / business income',profit:'Operating profit',turnover_growth_percent:'Turnover growth (%)',margin_percent:'Profit margin (%)',fee_contribution:'Fee contribution'}).forEach(([key,label])=>{
        const row=document.createElement('tr'),heading=document.createElement('th');heading.scope='row';heading.textContent=label;row.append(heading);
        fee.years.forEach(year=>{const cell=document.createElement('td');cell.textContent=year[key]==null?'—':key.endsWith('_percent')?Number(year[key]).toFixed(2)+'%':rand(year[key]);row.append(cell);});byId('profit-margin-fee-years').append(row);
      });
      byId('profit-margin-fee-total').textContent='Five-year profit-margin valuation: '+rand(fee.total)+' excluding VAT. '+fee.status;
      byId('territory-affordable-ceiling').textContent='Affordable fee ceiling: '+(fee.affordable_ceiling==null?'Unavailable':rand(fee.affordable_ceiling))+' excluding VAT.';
      byId('territory-fair-fee').textContent=fee.fair_fee==null?'Unavailable pending correction of assumptions':rand(fee.fair_fee);
      byId('territory-fee-reduction').textContent=fee.reduction==null?'':Number(fee.reduction)>0?'Reduction for affordability: '+rand(fee.reduction)+'.':'The five-year valuation is within the affordable ceiling.';
      byId('territory-fee-checks').textContent=(fee.affordability?.reasons||[]).join(' ')+(fee.fair_fee!=null&&Number(fee.fair_fee)===0?' No positive territory fee is supported under these assumptions.':'');
      const apply=byId('apply-profit-margin-fee');if(apply)apply.disabled=!ctx.editable||calculatedProfitFee==null||!Number.isFinite(Number(calculatedProfitFee))||Number(calculatedProfitFee)>1e12;
    }

    if(result.research)renderResearch(result.research,result.research.context);
    if(result.calculated_inputs)Object.entries(result.calculated_inputs).forEach(([key,value])=>{const el=byId('input-'+key);if(el)setFieldValue(el,value);});
    if(result.area_estimate){const e=result.area_estimate;byId('area-estimate-status').textContent=`Selected area: ${e.inputs.population.toLocaleString('en-ZA')} modelled residents; about ${e.estimated_households.toLocaleString('en-ZA')} households; ${e.estimated_annual_deaths.toLocaleString('en-ZA')} estimated resident deaths/year. First projected month: ${Number(result.expected.months[0].services).toFixed(2)} funeral services; income ${rand(result.expected.months[0].revenue)}. ${e.mortality_verified?'Provincial mortality source checked.':'Mortality uses the dated Compass reference and still needs online verification.'} Whole selected boundary calculated, including all separate selected areas; overlaps count once and cut-outs are subtracted. Actual franchise income is unchanged.`;}
    if(result.calculated_scores){document.querySelectorAll('[data-score]').forEach(el=>el.value=result.calculated_scores[el.dataset.score]??'');Object.entries(result.calculated_score_evidence||{}).forEach(([key,value])=>document.querySelector(`[data-score-evidence="${key}"]`).value=value);}
    if(result.score_recommendations)renderScoreRecommendations(result.score_recommendations);
    byId('territory-results').hidden = false;
    byId('recommendation').textContent = `${result.scorecard.score === null ? 'Incomplete scorecard' : 'Commercial potential: '+Number(result.scorecard.score).toFixed(1)+'/100 ('+result.scorecard.band+')'}. ${result.recommended_fee === null ? 'No recommended price.' : 'Provisional price: '+rand(result.recommended_fee)+'.'} ${result.recommendation_reason}`;
    byId('calculation-errors').textContent = result.expected.errors.length ? 'Needs review: '+result.expected.errors.join('; ') : 'Existing agreement method and royalty brackets applied.';
    byId('forecast-years').replaceChildren();
    const metrics={revenue:'Income',direct:'Direct costs',royalty:'Monthly royalty expense',overhead:'Other operating expenses',operating:'Operating profit',tax:'Tax',cash_deductions:'Other cash deductions / reserve',repayment:'Territory repayments',retained:'Cash retained'};
    Object.entries(metrics).forEach(([key,label])=>{const row=document.createElement('tr'),heading=document.createElement('th');heading.scope='row';heading.textContent=label;row.append(heading);result.expected.years.forEach(year=>{const cell=document.createElement('td');cell.textContent=rand(year[key]);row.append(cell);});byId('forecast-years').append(row);});
    byId('forecast-summary').textContent = `Expected: ${result.expected.affordable ? 'funding and recovery checks pass' : 'funding/recovery requires review'}. Investment ${rand(result.expected.initial)}; five-year cash after investment ${rand(result.expected.ending_cash)}; additional working capital needed ${rand(result.expected.additional_funding)}. Payback: ${result.expected.payback_months === null ? 'beyond five years' : result.expected.payback_months+' months'}.`;
    byId('downside-summary').textContent = `Downside (20% lower sales): five-year cash after investment ${rand(result.downside.ending_cash)}; additional working capital needed ${rand(result.downside.additional_funding)}. Draft planning estimate; review source coverage and agreement boundaries.`;
    window.TerritoryAssessments?.refresh(result.assessment_html,result.assessments);
  }
  function invalidateScoreRecommendations(){
    window.TerritoryAssessments?.invalidate();
    scoreRecommendations=null;calculatedProfitFee=null;calculatedCommercialFee=null;const commercialApply=byId('apply-commercial-fee');if(commercialApply)commercialApply.disabled=true;const apply=byId('apply-profit-margin-fee');if(apply)apply.disabled=true;

    document.querySelectorAll('[data-apply-score-recommendation]').forEach(el=>el.disabled=true);
    byId('score-recommendations-status').textContent='Inputs or territory changed. Update recommendations before applying suggestions.';
  }
  function renderScoreRecommendations(data){
    scoreRecommendations=data;
    byId('score-recommendations-status').textContent=data.note;

    Object.entries(data.entries).forEach(([key,entry])=>{
      const container=document.querySelector(`[data-score-recommendation="${key}"]`);if(!container)return;container.replaceChildren();
      const title=document.createElement('p');title.textContent=entry.recommended_score==null?'Suggested score: review required.':`Suggested score: ${entry.recommended_score}/5 · ${entry.status==='planning_assumption'?'Neutral planning assumption':'Provisional'} · ${entry.confidence} confidence.`;container.append(title);
      [entry.action,entry.reason,...entry.context].forEach(text=>{const p=document.createElement('p');p.textContent=text;container.append(p);});
      const detail=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Scoring guidance and sources';detail.append(summary);
      const rubric=document.createElement('p');rubric.textContent=entry.rubric;detail.append(rubric);
      const dates=document.createElement('p');dates.textContent=`Data: ${entry.data_period}. Source checked: ${dateText(entry.source_checked_at||'not established')}.`;detail.append(dates);
      entry.sources.forEach(url=>sourceLink(detail,url));container.append(detail);
    });
  }
  async function send(url, body, signal=null) {
    const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Territory-CSRF': ctx.csrf }, body: JSON.stringify(body), signal });
    const result = await response.json().catch(() => ({ error: 'Request failed. Refresh the page and try again.' }));
    if (!response.ok) throw new Error(result.error || 'Request failed');
    return result;
  }
  async function run(save) {
    if(save&&researchPending){status('Research is still running. Wait for its result before saving this revision.');return;}
    if(boundaryBusy){status('Updating the territory border. Please wait before saving or calculating.');return;}
    if (busy){if(!save)pendingAutoForecast=true;else status('Wait for the current calculation before saving.');return;}
    const geometryRevision=boundaryRevision,figuresRevision=inputRevision;
    busy = true;saving=save;document.querySelector('.territory-page').inert=save; status(save ? 'Saving a planning revision…' : 'Calculating monthly cash flow…');
    try {
      const payload = collect();
      if (save) {
        const saved = await send(ctx.saveUrl, { version, payload }); version = saved.version;
        status('Revision '+version+' saved.');
        window.location.reload();
      } else {
        const result = await send(ctx.calculateUrl, payload);
        if(geometryRevision!==boundaryRevision||figuresRevision!==inputRevision){pendingAutoForecast=true;return;}
        render(result); renderSources(payload.sources); status('Forecast calculated for the selected territory. Save a revision to preserve this calculation.');
      }
    } catch (error) { status(error.message); }
    finally { busy = false;saving=false;document.querySelector('.territory-page').inert=false;if(pendingAutoForecast){pendingAutoForecast=false;run(false);} }
  }
  let mapHasFitted=false,pendingMapFit=null,outlineTimer=null,outlineKey='',outlineGeneration=0,outlineAbort=null;
  let boundaryRevision=0,boundaryBusy=false,googleLabels=[],googleAreaFeatures=new Map(),combinedGoogleFeature=null,combinedGeometryKey=null,areaSelectControls=new Map(),areaListKey=null;
  function pointInTerritory(point,g=geometry()){
    if(!point||!g)return false;
    const inRing=ring=>{let inside=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){
      const [xi,yi]=ring[i],[xj,yj]=ring[j];if((yi>point[1])!==(yj>point[1])&&point[0]<(xj-xi)*(point[1]-yi)/(yj-yi)+xi)inside=!inside;
    }return inside;};
    const polygons=g.type==='Polygon'?[g.coordinates]:g.type==='MultiPolygon'?g.coordinates:[];
    return polygons.some(rings=>inRing(rings[0])&&!rings.slice(1).some(inRing));
  }
  function nextAreaSelection(area,point=area.label_point){
    return area.selection==='exclude'?'include':area.selection==='include'||pointInTerritory(point)?'exclude':'include';
  }
  function areaDescription(area){
    return (area.description||[area.name,area.municipality,area.province,'Boundary '+(area.data_date||'date unknown')].filter(Boolean).join(' · '))+
      ' · '+(area.selection==='include'?'Included — click to remove / cut out':area.selection==='exclude'?'Cut out — click to include':pointInTerritory(area.label_point)?'Included within combined territory — click to cut out':'Neighbouring area — click to include');
  }
  function describeArea(area){byId('map-area-description').textContent=areaDescription(area);}
  function mapBounds(){
    let values;
    if(map?.getBounds()){const b=map.getBounds();values=[b.getSouthWest().lng(),b.getSouthWest().lat(),b.getNorthEast().lng(),b.getNorthEast().lat()];}
    else if(leafMap){const b=leafMap.getBounds();values=[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()];}
    else{const g=geometry()||areas[0]?.geometry;if(!g)return null;const b=L.geoJSON(g).getBounds();values=[b.getWest()-.02,b.getSouth()-.02,b.getEast()+.02,b.getNorth()+.02];}
    return [Math.max(16,values[0]),Math.max(-35.3,values[1]),Math.min(34,values[2]),Math.min(-22,values[3])].map(v=>Number(v.toFixed(4)));
  }
  function fitGeometry(g){
    if(!g)return;
    if(!leafMap&&!map){pendingMapFit=g;return;}
    pendingMapFit=null;
    if(leafMap)leafMap.fitBounds(L.geoJSON(g).getBounds(),{padding:[20,20]});
    else if(map){const b=L.geoJSON(g).getBounds();map.fitBounds({west:b.getWest(),south:b.getSouth(),east:b.getEast(),north:b.getNorth()},25);}
    mapHasFitted=true;
  }
  function mapLineStyle(zoom){
    return {weight:zoom<12?.6:zoom<14?.85:1.1,opacity:zoom<12?.3:zoom<14?.45:.6};
  }
  function labelLayout(candidates,zoom,project,width,height){
    if(zoom<12)return [];
    const placed=[],limit=zoom<13?12:zoom<14?20:32;
    const priority=area=>area.selection==='include'?0:area.selection==='exclude'?1:2;
    const sorted=candidates.filter(a=>a.kind==='suburb'&&a.label_point).slice().sort((a,b)=>priority(a)-priority(b)||a.name.localeCompare(b.name));
    for(const area of sorted){
      const point=project(area.label_point);if(!point)continue;
      const w=Math.min(128,Math.max(36,area.name.length*5.5+12)),h=20;
      const box={left:point.x-w/2,right:point.x+w/2,top:point.y-h/2,bottom:point.y+h/2};
      if(box.left<8||box.right>width-50||box.top<55||box.bottom>height-28)continue;
      if(placed.some(p=>box.left<p.box.right+8&&box.right>p.box.left-8&&box.top<p.box.bottom+6&&box.bottom>p.box.top-6))continue;
      placed.push({area,box});if(placed.length>=limit)break;
    }
    return placed.map(p=>p.area);
  }
  function addGoogleLabels(visibleAreas){
    googleLabels.forEach(label=>label.setMap(null));googleLabels=[];
    if(!map?.getProjection()||!map.getBounds())return;
    const projection=map.getProjection(),bounds=map.getBounds(),scale=2**map.getZoom();
    const nw=projection.fromLatLngToPoint(new google.maps.LatLng(bounds.getNorthEast().lat(),bounds.getSouthWest().lng()));
    const div=map.getDiv();
    const candidates=labelLayout(visibleAreas,map.getZoom(),point=>{
      const p=projection.fromLatLngToPoint(new google.maps.LatLng(point[1],point[0]));
      return {x:(p.x-nw.x)*scale,y:(p.y-nw.y)*scale};
    },div.clientWidth,div.clientHeight);
    class AreaLabel extends google.maps.OverlayView{
      constructor(area){super();this.area=area;this.setMap(map);}
      onAdd(){this.div=document.createElement('span');this.div.className='territory-area-label'+(this.area.selection==='include'?' territory-area-label-selected':'');this.div.textContent=this.area.name;this.getPanes().overlayLayer.append(this.div);}
      draw(){if(!this.div)return;const position=this.getProjection().fromLatLngToDivPixel(new google.maps.LatLng(this.area.label_point[1],this.area.label_point[0]));this.div.style.left=position.x+'px';this.div.style.top=position.y+'px';}
      onRemove(){this.div?.remove();}
    }
    candidates.forEach(area=>googleLabels.push(new AreaLabel(area)));
  }
  function preview(fit=false){
    const g=geometry(),visibleAreas=areas;
    if(leafMap){
      leafParts.clearLayers();leafTerritory.clearLayers();
      const line=mapLineStyle(leafMap.getZoom());
      const labels=new Set(labelLayout(visibleAreas,leafMap.getZoom(),point=>leafMap.latLngToContainerPoint([point[1],point[0]]),leafMap.getSize().x,leafMap.getSize().y).map(a=>a.id));
      visibleAreas.forEach(area=>{
        const included=area.selection==='include',excluded=area.selection==='exclude';
        const layer=L.geoJSON(area.geometry,{style:{color:excluded?'#b42318':'#587f9c',weight:included?0:excluded?1.5:line.weight,opacity:excluded?.8:line.opacity,dashArray:excluded?'5 4':'',fillOpacity:included?0.001:0},bubblingMouseEvents:false})
          .on('mouseover',()=>describeArea(area))
          .on('click',event=>{L.DomEvent.stopPropagation(event);describeArea(area);if(ctx.editable){if(['town','municipality'].includes(area.kind))addSuburbAt([event.latlng.lng,event.latlng.lat]);else changeAreaSelection(area,nextAreaSelection(area,[event.latlng.lng,event.latlng.lat]));}}).addTo(leafParts);
        if(labels.has(area.id)){const span=document.createElement('span');span.textContent=area.name;L.tooltip({permanent:true,direction:'center',className:'territory-area-label',interactive:false}).setLatLng([area.label_point[1],area.label_point[0]]).setContent(span).addTo(leafParts);}
      });
      if(g)L.geoJSON(g,{style:{color:'#4a246a',weight:2.8,fillOpacity:0.10},interactive:false}).addTo(leafTerritory);
      if((fit||!mapHasFitted)&&(pendingMapFit||g||areas[0]?.geometry))fitGeometry(pendingMapFit||g||areas[0].geometry);
      byId('map-status').textContent=googleUnavailable?'Google Maps is unavailable; editing the same boundary over OpenStreetMap.':'Select suburb outlines to update one combined border. Roads © OpenStreetMap contributors.';
      return;
    }
    if(!map)return;
    const active=new Set(visibleAreas.map(a=>a.id));
    googleAreaFeatures.forEach((feature,id)=>{if(!active.has(id)){map.data.remove(feature);googleAreaFeatures.delete(id);}});
    visibleAreas.forEach(area=>{
      let feature=googleAreaFeatures.get(area.id);
      if(!feature){feature=map.data.addGeoJson({type:'Feature',geometry:area.geometry,properties:{area_id:area.id,selection:area.selection,kind:area.kind}})[0];googleAreaFeatures.set(area.id,feature);}
      else if(feature.getProperty('selection')!==area.selection)feature.setProperty('selection',area.selection);
    });
    const geometryKey=byId('geometry').value;
    if(geometryKey!==combinedGeometryKey){
      if(combinedGoogleFeature)map.data.remove(combinedGoogleFeature);
      combinedGoogleFeature=g?map.data.addGeoJson({type:'Feature',geometry:g,properties:{combined:true}})[0]:null;
      combinedGeometryKey=geometryKey;
    }
    if((fit||!mapHasFitted)&&(pendingMapFit||g||areas[0]?.geometry))fitGeometry(pendingMapFit||g||areas[0].geometry);
    addGoogleLabels(visibleAreas);
    byId('map-status').textContent='Google road map · blue outlines are neighbouring areas; the purple shaded interior is included in the calculations; its border is the combined territory. Hover for descriptions; click a suburb to include or remove it.';
  }
  function scheduleOutlines(){
    clearTimeout(outlineTimer);
    if(!ctx.editable||namedSearchPending)return;
    outlineTimer=setTimeout(()=>findAreas(true,true),650);
  }
  window.initTerritoryMap=()=>{
    if(googleUnavailable)return;
    map=new google.maps.Map(byId('territory-map'),{center:{lat:-28.48,lng:24.67},zoom:5,mapTypeId:'roadmap',clickableIcons:false,styles:[{featureType:'poi.business',elementType:'labels',stylers:[{visibility:'off'}]}]});
    map.data.setStyle(feature=>{
      if(feature.getProperty('combined'))return {fillColor:'#6B4AA0',fillOpacity:.10,strokeColor:'#4a246a',strokeWeight:2.8,strokeOpacity:.95,clickable:false,zIndex:3};
      const included=feature.getProperty('selection')==='include',excluded=feature.getProperty('selection')==='exclude';
      const line=mapLineStyle(map.getZoom());
      return {fillColor:excluded?'#b42318':'#b9ccda',fillOpacity:included?.001:0,strokeColor:excluded?'#b42318':'#587f9c',strokeWeight:included?0:excluded?1.5:line.weight,strokeOpacity:excluded?.8:line.opacity,clickable:true,zIndex:['town','municipality'].includes(feature.getProperty('kind'))?0:included?2:1};
    });
    map.data.addListener('mouseover',event=>{const area=areas.find(a=>a.id===event.feature.getProperty('area_id'));if(area)describeArea(area);});
    map.data.addListener('click',event=>{const area=areas.find(a=>a.id===event.feature.getProperty('area_id'));if(!area)return;describeArea(area);if(!ctx.editable)return;if(['town','municipality'].includes(area.kind))addSuburbAt([event.latLng.lng(),event.latLng.lat()]);else changeAreaSelection(area,nextAreaSelection(area,[event.latLng.lng(),event.latLng.lat()]));});
    map.addListener('click',event=>{if(ctx.editable&&event.latLng)addSuburbAt([event.latLng.lng(),event.latLng.lat()]);});
    map.addListener('idle',()=>{map.data.revertStyle();addGoogleLabels(areas);scheduleOutlines();});
    try{preview(true);}catch(error){status(error.message);}
  };
  function useRoadFallback(){
    if(leafMap||!window.L)return;
    googleLabels.forEach(label=>label.setMap(null));googleLabels=[];googleAreaFeatures.clear();combinedGoogleFeature=null;combinedGeometryKey=null;map=null;byId('territory-map').replaceChildren();
    leafMap=L.map('territory-map').setView([-28.48,24.67],5);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'© OpenStreetMap contributors'}).on('tileerror',()=>{byId('map-status').textContent='Some road tiles are unavailable. Boundary geometry remains visible.';}).addTo(leafMap);
    leafParts=L.layerGroup().addTo(leafMap);leafTerritory=L.layerGroup().addTo(leafMap);
    leafMap.on('moveend',()=>{preview();scheduleOutlines();});leafMap.on('click',event=>{if(ctx.editable)addSuburbAt([event.latlng.lng,event.latlng.lat]);});preview(true);scheduleOutlines();
  }
  window.gm_authFailure=()=>{googleUnavailable=true;useRoadFallback();};
  window.territoryMapsLoadFailed=window.gm_authFailure;
  function invalidateResearch(message){
    invalidateScoreRecommendations();researchGeneration++;researchPending=false;researchRunId=null;researchResult=null;servicePriceSelections={};
    ['research-records','research-businesses','research-prices'].forEach(id=>byId(id).replaceChildren());
    byId('area-estimate-status').textContent='Territory inputs changed; updated figures are pending calculation.';
    byId('input-confirmed').checked=false;byId('territory-results').hidden=true;byId('apply-research').hidden=true;
    byId('research-status').textContent=message||'Territory changed. Area figures are recalculated; refresh public research before accepting a valuation.';
  }
  function renderSelectionSummary(){
    const selected=areas.filter(a=>a.selection!=='context'),included=selected.filter(a=>a.selection==='include');
    byId('territory-selection-summary').textContent=included.length?`Current territory: ${included.length} included area(s), ${selected.length-included.length} explicit cut-out(s). A new town search replaces these selections in the draft; saved revisions remain in history.`:'No areas included. Search for a place, then select its suburbs on the map.';
    const list=byId('territory-selected-areas');list.replaceChildren();
    selected.forEach(area=>{
      const button=document.createElement('button');button.type='button';button.className='territory-selection-chip';button.textContent=(area.selection==='exclude'?'Cut-out: ':'')+area.name+' ×';
      button.setAttribute('aria-label','Remove '+area.name+(area.selection==='exclude'?' cut-out':' from territory'));
      button.disabled=!ctx.editable;button.addEventListener('click',()=>changeAreaSelection(area,'context'));list.append(button);
    });
  }
  function renderAreas(){
    renderSelectionSummary();
    const container=byId('boundary-areas');if(!container)return;
    const nextKey=JSON.stringify(areas.map(a=>a.id));
    if(nextKey!==areaListKey){
      container.replaceChildren();areaSelectControls.clear();areaListKey=nextKey;
      areas.forEach(area=>{
        const row=document.createElement('div');row.className='territory-area-row';
        const label=document.createElement('label');label.textContent=(area.description||[area.name,area.municipality,area.province,area.data_date].filter(Boolean).join(' · '))+(area.upcoming?' · Upcoming':'')+' ';
        const select=document.createElement('select');select.disabled=!ctx.editable;
        [['context','Context only'],['include','Include'],['exclude','Exclude / cut out']].forEach(([value,text])=>{const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);});
        select.value=area.selection;areaSelectControls.set(area.id,select);
        select.addEventListener('change',()=>{const current=areas.find(a=>a.id===area.id);if(current)changeAreaSelection(current,select.value);});label.append(select);row.append(label);sourceLink(row,area.source,'Boundary source');container.append(row);
      });
    }else areas.forEach(area=>{const control=areaSelectControls.get(area.id);if(control)control.value=area.selection;});
    byId('whole-area-note').hidden=!areas.some(a=>['town','municipality'].includes(a.kind)&&a.selection==='include');
    preview();
  }
  async function changeAreaSelection(area,next){
    if(saving)return;
    if(next==='include'&&!areas.some(a=>a.selection==='include')&&area.province){byId('research-province').value=area.province;byId('research-title').value=area.name;byId('research-municipality').value=area.municipality_code||'';}
    area.selection=next;describeArea(area);renderAreas();
    const successful=await composeBoundary();
    if(!successful){areas.forEach(a=>a.selection=appliedSelections.get(a.id)||'context');renderAreas();}
  }
  async function addSuburbAt(point){
    if(saving)return;
    // Google may dispatch a click through a transparent suburb to the town or
    // base map. Resolve the already sourced polygons before any network lookup.
    const loaded=areas.filter(a=>a.kind==='suburb'&&pointInTerritory(point,a.geometry));
    if(loaded.length===1){
      await changeAreaSelection(loaded[0],nextAreaSelection(loaded[0],point));
      byId('boundary-status').textContent=areaDescription(loaded[0]);
      return;
    }
    if(loaded.length>1){
      byId('boundary-status').textContent='More than one loaded suburb boundary meets this location. Choose the intended area in the list.';
      return;
    }
    try{
      byId('boundary-status').textContent='Finding the published suburb boundary at this location…';
      const result=await send(ctx.boundaryUrl,{kind:'suburb',point});
      const known=new Map(areas.map(a=>[a.id,a]));result.areas.forEach(a=>{if(!known.has(a.id)){areas.push(a);known.set(a.id,a);}});
      renderAreas();
      if(result.areas.length===1){const area=known.get(result.areas[0].id);await changeAreaSelection(area,nextAreaSelection(area,point));byId('boundary-status').textContent=areaDescription(area);}
      else byId('boundary-status').textContent=result.areas.length?'More than one boundary meets this location. Choose the correct area in the list.':'No published suburb polygon was found here. Search by name; no boundary has been invented.';
    }catch(error){byId('boundary-status').textContent=error.message;}
  }
  async function findAreas(nearby=false,automatic=false){
    if(automatic&&namedSearchPending)return;
    let generation,completed=false;
    try{
      let body={query:byId('boundary-query').value,kind:byId('boundary-kind').value,release:byId('boundary-release').value};
      if(nearby){
        const bounds=mapBounds();if(!bounds)return;
        if(bounds[2]-bounds[0]>1.2||bounds[3]-bounds[1]>1.2){byId('boundary-status').textContent='Zoom closer to load neighbouring suburb outlines. Named searches remain available.';return;}
        const key=bounds.join(',');if(automatic&&outlineKey===key)return;outlineKey=key;body={kind:'suburb',bounds};
      }
      generation=++outlineGeneration;
      if(!automatic){namedSearchPending=true;namedSearchGeneration=generation;clearTimeout(outlineTimer);}
      outlineAbort?.abort();outlineAbort=new AbortController();
      byId('boundary-status').textContent=nearby?'Loading all available neighbouring area outlines in this map view…':'Checking the selected boundary publisher and release…';
      const result=await send(ctx.boundaryUrl,body,outlineAbort.signal);if(generation!==outlineGeneration)return;
      if(!nearby){
        if(!result.areas.length){byId('boundary-status').textContent='No matching area found. The current territory has been kept; try a more specific town name.';completed=true;return;}
        // A successful named search starts a fresh draft, not an addition to the old town.
        byId('geometry').value='';byId('boundary-note').value='';
        areas=result.areas.map(area=>({...area,selection:'context'}));
        const query=byId('boundary-query').value.trim().toLocaleLowerCase();
        const exact=areas.filter(a=>a.name.trim().toLocaleLowerCase()===query);
        const chosen=areas.length===1?areas[0]:exact.length===1?exact[0]:null;
        if(chosen){chosen.selection='include';if(scenarioMode==='prospective')byId('prospect-name').value=chosen.name;byId('research-title').value=chosen.name;byId('research-province').value=chosen.province;byId('research-municipality').value=chosen.municipality_code||'';}
        else{byId('research-title').value=byId('boundary-query').value;}
        // Other source findings and public prices belong to the previous location.
        byId('sources').value='[]';renderSources([]);
        renderAreas();fitGeometry((chosen||areas[0]).geometry);
        if(!await composeBoundary()){byId('boundary-status').textContent='The new area was found, but its boundary could not be calculated. Retry before saving.';completed=true;return;}
        byId('boundary-status').textContent=chosen?`New territory: ${chosen.name}. Previous town selections have been removed from this draft. ${result.message}`:
          `${areas.length} matching areas found. Previous selections have been cleared. Include the intended area(s) to calculate a new territory. ${result.message}`;
      }else{
        const returned=new Set(result.areas.map(a=>a.id));
        areas=areas.filter(a=>a.selection!=='context'||a.kind!=='suburb'||returned.has(a.id));
        const known=new Set(areas.map(a=>a.id));result.areas.forEach(a=>{if(!known.has(a.id)){areas.push(a);known.add(a.id);}});
        byId('boundary-status').textContent=`${result.areas.length} neighbouring outlines loaded. ${result.message}`;
      }
      renderAreas();completed=true;
    }catch(error){if(error.name==='AbortError')return;if(generation===outlineGeneration||generation==null){outlineKey='';byId('boundary-status').textContent=error.message;}}
    finally{if(!automatic&&namedSearchGeneration===generation){namedSearchPending=false;if(completed)scheduleOutlines();}}
  }
  function boundaryNote(parts){
    const summary=parts.slice(0,35).map(a=>`${String(a.name).slice(0,80)}: ${a.selection}; boundary ${String(a.data_date||'unknown').slice(0,20)}`).join('\n');
    return `${parts.length} selected geographical parts. Full boundaries, source links, release dates and inclusion/cut-out selections are preserved with every part in the saved revision.\n${summary}${parts.length>35?'\nAdditional areas are listed in the boundary sources in the saved report.':''}`;
  }
  async function composeBoundary(){
    if(saving)return true;
    const revision=++boundaryRevision;boundaryBusy=true;invalidateResearch();
    try{
      const selected=areas.filter(a=>a.selection!=='context');
      if(!selected.some(a=>a.selection==='include')){byId('geometry').value='';byId('boundary-note').value='';setFieldValue(byId('input-population'),0);appliedSelections=new Map();preview();byId('area-estimate-status').textContent='No territory selected. Select an area to calculate population, demand and income.';status('No territory selected. Click suburb outlines to build the combined border.');return true;}
      const result=await send(ctx.composeUrl,{parts:selected,include_enclosed:true});if(revision!==boundaryRevision)return true;
      includeEnclosed=true;
      byId('geometry').value=JSON.stringify(result.geometry);appliedSelections=new Map(areas.map(a=>[a.id,a.selection]));
      byId('boundary-note').value=boundaryNote(selected)+'\nEnclosed interiors are automatically included; explicitly excluded cut-outs are subtracted. No land between disconnected areas is added.';
      preview();status('Combined territory border updated. Shared internal edges are removed; enclosed interiors are included and explicit cut-outs remain.');
      byId('input-demand_basis').value='territory';boundaryBusy=false;await run(false);
      return true;
    }catch(error){if(revision!==boundaryRevision)return true;status(error.message);return false;}
    finally{if(revision===boundaryRevision)boundaryBusy=false;}
  }

  function sourceLink(container, url, label='Source') {
    try {const parsed=new URL(url);if(parsed.protocol!=='https:')return;const link=document.createElement('a');link.href=parsed.href;link.textContent=label;link.target='_blank';link.rel='noopener';container.append(link,document.createTextNode(' '));}catch(_){}
  }
  function renderResearch(result, requestContext) {
    researchResult=result;
    const container=byId('research-records');container.replaceChildren();
    (result.records||[]).forEach(record=>{
      const card=document.createElement('div');card.className='form-section-card';
      const h=document.createElement('h3');h.textContent=record.label+': '+(record.value===null?'Unknown':record.value+' '+record.unit);card.append(h);
      const summary=document.createElement('p');const states={available:'Retrieved from publisher',verified_reference:'Verified official release archive',cached_reference:'Stored source dataset',cached_unverified:'Compass reference; online refresh unavailable',partial:'Partial coverage',unavailable:'Not available'};
      summary.textContent=record.data_period+' · '+record.confidence+' confidence · '+(states[record.status]||record.status);card.append(summary);
      const details=document.createElement('details');const heading=document.createElement('summary');heading.textContent='Method, geographic coverage and check date';details.append(heading);
      const checked=dateText(record.retrieved_at);
      [record.confidence_reason,'Scope: '+record.geographic_scope,'Method: '+record.method,'Checked / processed: '+checked+' SAST'].forEach(text=>{const p=document.createElement('p');p.textContent=text;details.append(p);});card.append(details);
      (record.sources||[]).forEach(url=>sourceLink(card,url));container.append(card);
    });
    (result.warnings||[]).forEach(w=>{const p=document.createElement('p');p.textContent=w;container.append(p);});
    const businesses=byId('research-businesses');businesses.replaceChildren();
    (result.operators||[]).forEach(o=>{const p=document.createElement('p');p.textContent=o.name+' · '+(o.address||'')+' · '+(o.territory_relation||'mapped point inside territory')+' · '+(o.business_role||'')+' · '+(o.verification||'')+' ';sourceLink(p,o.source,'Business source');businesses.append(p);});
    const prices=byId('research-prices');prices.replaceChildren();
    (result.public_prices||[]).forEach((p,sourceIndex)=>{const card=document.createElement('div');const h=document.createElement('h3');h.textContent=p.business+' — public price findings';card.append(h);
      const note=document.createElement('p');note.textContent=(p.coverage||'Selected-territory coverage unverified')+'. Checked '+p.checkedAt+'. '+p.scope;card.append(note);
      (p.candidates||[]).forEach((c,candidateIndex)=>{const row=document.createElement('p');row.textContent=rand(c.price)+' · '+c.kind+' · '+c.context+' ';sourceLink(row,c.source||p.url,'Price source');
        if(ctx.editable && !c.excluded && /cash package candidate/i.test(c.kind||'')){
          const button=document.createElement('button');button.type='button';button.className='btn';button.textContent='Use as '+(c.serviceType==='cremation'?'cremation':'funeral-service')+' price';
          button.addEventListener('click',()=>{if(saving||!researchRunId)return;inputRevision++;invalidateScoreRecommendations();const type=c.serviceType==='cremation'?'cremation':'burial';const field=byId(type==='cremation'?'input-cremation_price':'input-burial_price');setFieldValue(field,c.price);servicePriceSelections[type]={run_id:researchRunId,source_index:sourceIndex,candidate_index:candidateIndex};byId('input-confirmed').checked=false;byId('territory-results').hidden=true;button.textContent='Included in funeral-service forecast';status(rand(c.price)+' included as the '+type+' service price. Its source will appear in the saved report. Review package inclusions and local availability.');run(false);});row.append(button);
        }card.append(row);});
      (p.materials||[]).forEach(m=>{const row=document.createElement('p');row.textContent=m.kind+' · '+m.status+' ';sourceLink(row,m.url,m.label||'Original material');card.append(row);});prices.append(card);});
    if(requestContext){byId('research-title').value=requestContext.title||'';byId('research-province').value=requestContext.province;byId('research-municipality').value=requestContext.municipality_code||'';}
    byId('apply-research').hidden=!ctx.editable || !Object.keys(result.proposed_inputs||{}).length;
  }
  async function researchTerritory() {
    if(saving)return;
    invalidateResearch('Research is being refreshed for the selected territory.');
    const generation=researchGeneration;researchPending=true;inputRevision++;
    invalidateScoreRecommendations();researchRunId=null;servicePriceSelections={};byId('apply-research').hidden=true;
    try{
      const inputs=collect().inputs;
      const submitted={geometry:geometry(),province:byId('research-province').value,municipality_code:byId('research-municipality').value.toUpperCase().trim(),household_size:inputs.household_size,market_share:inputs.market_share,title:byId('research-title').value,area_names:areas.filter(a=>a.selection==='include').map(a=>a.name).slice(0,20)};
      const started=await send(ctx.researchUrl,submitted);
      async function poll(){
        if(generation!==researchGeneration)return;
        try{
          const response=await fetch(ctx.researchUrl+'/'+started.run_id);const state=await response.json();if(generation!==researchGeneration)return;if(!response.ok)throw Error(state.error||'Cannot read research progress');
          byId('research-status').textContent=state.status+' · '+state.progress+'% · '+state.message;
          if(state.status==='completed'){researchPending=false;inputRevision++;byId('input-confirmed').checked=false;researchRunId=state.run_id;renderResearch(state.result,state.context);await run(false);status('Research ready. Review findings, apply estimates if appropriate, then save a revision for its report.');}
          else if(!['failed','cancelled'].includes(state.status)){setTimeout(poll,4000);}else researchPending=false;
        }catch(error){if(generation===researchGeneration){researchPending=false;byId('research-status').textContent=error.message;}}
      }
      await poll();
    }catch(error){if(generation===researchGeneration){researchPending=false;byId('research-status').textContent=error.message;}}
  }
  byId('find-boundaries')?.addEventListener('click',()=>findAreas(false));
  byId('start-suburb-selection')?.addEventListener('click',async()=>{areas.forEach(a=>{if(['town','municipality'].includes(a.kind))a.selection='context';});renderAreas();await composeBoundary();byId('map-area-description').textContent='Suburb selection mode: click the visible suburb outlines to build the combined territory. Neighbouring areas remain visible.';});
  byId('get-score-recommendations')?.addEventListener('click',async()=>{try{byId('score-recommendations-status').textContent='Reviewing the current territory evidence and franchise baseline…';await run(false);}catch(error){invalidateScoreRecommendations();byId('score-recommendations-status').textContent=error.message;}});
  byId('start-prospective')?.addEventListener('click',async()=>{
    if(saving||busy||boundaryBusy){status('Wait for the current calculation before starting a prospective scenario.');return;}
    const requestBoundary=boundaryRevision,requestInputs=inputRevision;
    try{
      const data=await send(ctx.prospectUrl,{geometry:geometry(),boundary_parts:areas.filter(a=>a.selection!=='context'),
        boundary_note:byId('boundary-note').value,area_province:byId('research-province').value,include_enclosed:includeEnclosed,
        name:byId('research-title').value||'Prospective territory'});
      if(saving||requestBoundary!==boundaryRevision||requestInputs!==inputRevision){status('Area or inputs changed while suggestions loaded. Generate suggestions again for the current territory.');return;}
      inputRevision++;scenarioMode='prospective';financialBaseline=null;comparisonBaseline=null;researchRunId=null;researchResult=null;servicePriceSelections={};
      invalidateResearch('New prospective area scenario. Refresh public research for local service-price evidence.');
      Object.entries(data.payload.inputs).forEach(([key,value])=>{const el=byId('input-'+key);if(el){if(el.type==='checkbox')el.checked=value;else setFieldValue(el,value);}});
      byId('prospect-name').value=data.payload.prospect.name;setFieldValue(byId('prospect-royalty'),data.payload.prospect.royalty_percent);
      byId('sources').value='[]';renderSources([]);byId('financial-baseline-status').textContent='Area suggestions loaded. No holder baseline is attached.';
      render(data.result);status('Area-only suggestions calculated. Review the assumptions and both territory fees before saving.');
    }catch(error){status(error.message);}
  });
  byId('load-running-concern')?.addEventListener('click',async()=>{
    if(saving)return;const requestInputs=inputRevision;
    try{
      const data=await send(ctx.actualsUrl,{});if(saving||requestInputs!==inputRevision){byId('running-concern-status').textContent='Inputs changed while the comparison loaded. Load it again to attach it.';return;}inputRevision++;comparisonBaseline=data.fingerprint;
      byId('running-concern-status').textContent='Loading the separate recorded-business comparison…';await run(false);
    }catch(error){byId('running-concern-status').textContent=error.message;}
  });
  ['prospect-name','prospect-royalty'].forEach(id=>byId(id)?.addEventListener('change',()=>{
    if(scenarioMode!=='prospective'||saving)return;inputRevision++;byId('input-confirmed').checked=false;invalidateScoreRecommendations();renderScenario();run(false);
  }));
  byId('use-franchise-figures')?.addEventListener('click',async()=>{
    try{
      const data=await send(ctx.actualsUrl,{});
      byId('financial-baseline-status').textContent=`${data.observed_months}/12 approved months (${dateText(data.window_start)} to ${dateText(data.window_end)}). ${data.warnings.join(' ')}`;
      if(!Object.keys(data.proposed_inputs).length)return;
      inputRevision++;scenarioMode='existing';comparisonBaseline=null;financialBaseline=data.fingerprint;servicePriceSelections={};
      invalidateScoreRecommendations();
      Object.entries(data.proposed_inputs).forEach(([key,value])=>{const el=byId('input-'+key);if(el){if(el.type==='checkbox')el.checked=value;else setFieldValue(el,value);}});
      byId('territory-results').hidden=true;status('Recorded franchise baseline applied with 70% expenses and Compass repayment assumptions. Review the refreshed forecast before saving.');await run(false);
    }catch(error){byId('financial-baseline-status').textContent=error.message;}
  });
  byId('find-suburbs')?.addEventListener('click',()=>findAreas(true));
  byId('compose-boundary')?.addEventListener('click',composeBoundary);
  byId('research-territory')?.addEventListener('click',researchTerritory);
  byId('apply-research')?.addEventListener('click',()=>{if(!researchRunId||!researchResult)return;inputRevision++;invalidateScoreRecommendations();Object.entries(researchResult?.proposed_inputs||{}).forEach(([key,value])=>{const input=byId('input-'+key);if(input)setFieldValue(input,value);});byId('input-confirmed').checked=false;byId('territory-results').hidden=true;status('Research estimates applied. Review the source notes and financial assumptions before confirming.');run(false);});
  byId('add-source')?.addEventListener('click',()=>{inputRevision++;byId('input-confirmed').checked=false;const sources=JSON.parse(byId('sources').value);sources.push({metric:byId('evidence-factor').value,value:byId('evidence-value').value,url:byId('evidence-url').value,data_date:byId('evidence-date').value,retrieved_at:new Date().toISOString(),confidence:byId('evidence-confidence').value,method:byId('evidence-method').value});byId('sources').value=JSON.stringify(sources);renderSources(sources);byId('territory-results').hidden=true;status('Reviewed source recorded. Save a revision to preserve it.');});
  byId('geometry').addEventListener('input',()=>{areas=[];invalidateResearch();renderAreas();});
  byId('geometry').addEventListener('change',()=>{try{preview();if(geometry())run(false);}catch(error){status(error.message);}});
  byId('input-market_share').addEventListener('input',()=>invalidateResearch('Market share changed. Rerun research to update the demand estimate.'));
  ['research-province','research-municipality','research-title'].forEach(id=>byId(id).addEventListener('change',()=>{inputRevision++;invalidateResearch('Research context changed. Refresh findings for the selected province and municipality.');if(id==='research-province'&&geometry())run(false);}));
  if(!ctx.hasMaps)useRoadFallback();
  byId('show-selected-territory')?.addEventListener('click',()=>fitGeometry(geometry()));
  byId('preview-boundary')?.addEventListener('click', () => { try { preview(); if(geometry())run(false); } catch (error) { status(error.message); } });
  byId('territory-form').addEventListener('submit', event => { event.preventDefault(); run(false); });
  byId('save-territory')?.addEventListener('click', () => run(true));
  byId('input-fee_method')?.addEventListener('change',()=>{if(saving)return;inputRevision++;byId('input-confirmed').checked=false;if(byId('input-fee_method').value==='commercial')setFieldValue(byId('input-territory_fee'),ctx.commercialFees[byId('input-commercial_category').value]);invalidateScoreRecommendations();run(false);});
  byId('input-commercial_category')?.addEventListener('change',()=>{
    if(saving)return;inputRevision++;byId('input-confirmed').checked=false;
    if(byId('input-fee_method').value==='commercial')setFieldValue(byId('input-territory_fee'),ctx.commercialFees[byId('input-commercial_category').value]);
    invalidateScoreRecommendations();run(false);
  });
  byId('apply-commercial-fee')?.addEventListener('click',()=>{
    if(saving||busy||calculatedCommercialFee==null)return;
    const amount=calculatedCommercialFee;inputRevision++;byId('input-fee_method').value='commercial';
    setFieldValue(byId('input-territory_fee'),amount);byId('input-confirmed').checked=false;invalidateScoreRecommendations();run(false);
  });
  byId('apply-profit-margin-fee')?.addEventListener('click',()=>{
    if(saving||busy||calculatedProfitFee==null)return;
    const amount=calculatedProfitFee;
    inputRevision++;byId('input-fee_method').value='fair_territory';setFieldValue(byId('input-territory_fee'),amount);
    byId('input-confirmed').checked=false;invalidateScoreRecommendations();run(false);
  });

  document.querySelectorAll('[data-input],[data-score],[data-score-evidence],#geometry,#sources,#boundary-note').forEach(el => el.addEventListener('input', () => { inputRevision++;window.TerritoryAssessments?.invalidate();if(el.dataset.input && el.dataset.input!=='confirmed'){byId('input-confirmed').checked=false;invalidateScoreRecommendations();}byId('territory-results').hidden = true; status('Inputs changed. Review assumptions and recalculate before reviewing the forecast.'); }));
  renderScenario();
  renderSources(ctx.payload.sources || []);
  if (ctx.savedResult) render(ctx.savedResult);
  if(ctx.savedResult?.financial_baseline){const b=ctx.savedResult.financial_baseline;byId('financial-baseline-status').textContent=`Saved baseline: ${b.observed_months}/12 approved months (${dateText(b.window_start)} to ${dateText(b.window_end)}). ${b.warnings.join(' ')}`;}
  renderAreas();
  if(researchResult)renderResearch(researchResult,researchResult.context);
  if(ctx.editable&&geometry()){if(!includeEnclosed&&areas.some(a=>a.selection==='include'))composeBoundary();else run(false);}
})();
