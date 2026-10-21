const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function harness(initialPayload={},fields=[]){
  const elements=new Map(),requests=[];
  const element=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',hidden:true,checked:false,inert:false,dataset:{},addEventListener(type,handler){(this.listeners??={})[type]=handler;},replaceChildren(){this.children=[];},append(...items){(this.children??=[]).push(...items);},setAttribute(){},setCustomValidity(message){this.validationMessage=message;}});return elements.get(id);};
  element('territory-context').textContent=JSON.stringify({payload:{sources:[],boundary_parts:[],...initialPayload},hasMaps:true,editable:true,calculateUrl:'/calculate',saveUrl:'/save',composeUrl:'/compose',researchUrl:'/research'});
  element('research-province').value='Gauteng';
  fields.forEach(spec=>Object.assign(element('input-'+spec.dataset.input),spec));
  const fieldElements=fields.map(spec=>element('input-'+spec.dataset.input));
  const sandbox={document:{getElementById:element,querySelector:()=>element('page'),querySelectorAll:selector=>['[data-input]','[data-format]'].includes(selector)?fieldElements:[],createTextNode:text=>({textContent:text}),createElement:()=>element(Symbol())},window:{location:{reload(){throw Error('Unexpected reload');}}},Intl,URL,AbortController,setTimeout,clearTimeout,fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))};
  sandbox.globalThis=sandbox;
  let source=fs.readFileSync('app/static/js/territory.js','utf8');
  source=source.replace(/\}\)\(\);\s*$/,`globalThis.api={addSuburbAt,pointInTerritory,nextAreaSelection,preview,installMapForTest:stub=>{map=stub;mapHasFitted=true;},labelLayout,mapLineStyle,getAreas:()=>areas,dateText,fieldNumber,setFieldValue,collect,fitGeometry,pendingFit:()=>pendingMapFit,hasFitted:()=>mapHasFitted,findAreas,namedPending:()=>namedSearchPending,invalidateScoreRecommendations,renderResearch,boundaryNote,run,changeAreaSelection,invalidateResearch,researchTerritory,bump:()=>inputRevision++,pending:()=>researchPending};})();`);
  vm.runInNewContext(source,sandbox);
  return {api:sandbox.api,element,requests};
}
const result={scorecard:{score:null},recommended_fee:null,recommendation_reason:'Draft',expected:{errors:[],years:[],initial:0,ending_cash:0,additional_funding:0,payback_months:null},downside:{ending_cash:0,additional_funding:0}};
const finish=(request,body,ok=true)=>request.resolve({ok,json:async()=>body});
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('late forecast cannot overwrite newer input state',async()=>{
  const h=harness();
  const first=h.api.run(false);
  assert.equal(h.requests.length,1);
  h.api.bump();finish(h.requests[0],result);await first;await tick();
  assert.equal(h.requests.length,2);
  assert.equal(h.element('territory-results').hidden,true);
  finish(h.requests[1],result);await tick();
  assert.equal(h.element('territory-results').hidden,false);
});

test('save locks editing and releases the lock after failure',async()=>{
  const h=harness(),area={selection:'context'};
  const saving=h.api.run(true);
  assert.equal(h.element('page').inert,true);
  await h.api.changeAreaSelection(area,'include');
  assert.equal(area.selection,'context');
  finish(h.requests[0],{error:'Revision changed'},false);await saving;
  assert.equal(h.element('page').inert,false);
  assert.equal(h.element('territory-status').textContent,'Revision changed');
});

test('invalidating research cancels stale progress and old prices',async()=>{
  const h=harness();
  const researching=h.api.researchTerritory();
  assert.equal(h.api.pending(),true);
  h.api.invalidateResearch();
  assert.equal(h.api.pending(),false);
  finish(h.requests[0],{run_id:1,status:'queued'});await researching;await tick();
  assert.equal(h.requests.length,1);
  assert.equal(h.element('apply-research').hidden,true);
});


test('large multi-suburb territories retain a bounded human summary',()=>{
  const h=harness();
  const parts=Array.from({length:150},(_,i)=>({name:'Suburb '+i+' '+ 'a'.repeat(250),selection:'include',data_date:'2011-10-09',source:'https://example.com/'+i}));
  const before=JSON.stringify(parts);
  const note=h.api.boundaryNote(parts);
  assert.ok(note.length<=5000);
  assert.match(note,/150 selected geographical parts/);
  assert.equal(JSON.stringify(parts),before); // full source metadata stays intact
});


test('applying franchise figures automatically recalculates with the new baseline',async()=>{
  const h=harness();
  const applying=h.element('use-franchise-figures').listeners.click();
  assert.equal(h.requests.length,1);
  finish(h.requests[0],{observed_months:12,window_start:'2025-10',window_end:'2026-09',warnings:[],
    fingerprint:'approved-reference',proposed_inputs:{policy_receipts:20000,policy_payover:18000,expense_percent:70}});
  await tick();
  assert.equal(h.requests.length,2);
  assert.equal(h.requests[1].url,'/calculate');
  const body=JSON.parse(h.requests[1].options.body);
  assert.equal(body.financial_baseline,'approved-reference');
  assert.equal(h.element('input-policy_receipts').value,20000);
  assert.equal(h.element('territory-results').hidden,true);
  finish(h.requests[1],result);await applying;
  assert.equal(h.element('territory-results').hidden,false);
});


test('cash package selection recalculates immediately and preserves its source selection',async()=>{
  const h=harness({research_run_id:8});
  h.api.renderResearch({records:[],operators:[],public_prices:[{business:'Local provider',url:'https://example.com',
    candidates:[{price:9900,kind:'Cash package candidate',serviceType:'cremation',context:'Direct cremation'}],materials:[]}]},{});
  const findButton=node=>node.children?.flatMap(child=>[child,...(findButton(child)||[])])||[];
  const button=findButton(h.element('research-prices')).find(node=>node.textContent==='Use as cremation price');
  assert.ok(button);
  button.listeners.click();
  assert.equal(h.element('input-cremation_price').value,9900);
  assert.equal(h.requests.length,1);
  const data=JSON.parse(h.requests[0].options.body);
  assert.deepEqual(data.service_price_selections.cremation,{run_id:8,source_index:0,candidate_index:0});
  assert.equal(h.element('territory-results').hidden,true);
  finish(h.requests[0],result);await tick();
  assert.equal(h.element('territory-results').hidden,false);
});


test('optional fee is applied explicitly and stale calculations cannot be reused',async()=>{
  const h=harness();
  const calculation=h.api.run(false);
  finish(h.requests[0],{...result,profit_margin_fee:{selected:false,total:'200000.00',status:'Draft',fair_method_version:'v1',fair_fee:'120000.00',affordable_ceiling:'120000.00',reduction:'80000.00',affordability:{reasons:[]},
    years:Array.from({length:5},(_,i)=>({year:i+1,turnover:1000000,profit:200000,margin_percent:20,turnover_growth_percent:0,fee_contribution:40000}))}});
  await calculation;
  assert.equal(h.element('apply-profit-margin-fee').disabled,false);
  h.element('apply-profit-margin-fee').listeners.click();
  assert.equal(h.element('input-territory_fee').value,'120000.00');
  assert.equal(h.element('input-fee_method').value,'fair_territory');
  assert.equal(h.element('input-confirmed').checked,false);
  assert.equal(h.requests.length,2);
  assert.equal(h.element('apply-profit-margin-fee').disabled,true);
  finish(h.requests[1],result);await tick();
  h.api.invalidateScoreRecommendations();
  h.element('input-territory_fee').value='12345';
  h.element('apply-profit-margin-fee').listeners.click();
  assert.equal(h.element('input-territory_fee').value,'12345');
  assert.equal(h.requests.length,2);
});


test('background neighbour refresh cannot interrupt a named boundary search',async()=>{
  const h=harness();h.element('boundary-query').value='Randburg';h.element('boundary-kind').value='auto';
  const named=h.api.findAreas(false,false);
  assert.equal(h.api.namedPending(),true);
  assert.equal(h.requests.length,1);
  await h.api.findAreas(true,true);
  assert.equal(h.requests.length,1);
  finish(h.requests[0],{areas:[],message:'No matching boundary'});await named;
  assert.equal(h.api.namedPending(),false);
});


test('a town found before the map loads retains its requested map position',()=>{
  const h=harness();const geometry={type:'Polygon',coordinates:[[[27.9,-26.1],[28,-26.1],[28,-26],[27.9,-26.1]]]};
  h.api.fitGeometry(geometry);
  assert.equal(h.api.hasFitted(),false);
  assert.deepEqual(h.api.pendingFit(),geometry);
});


test('formatted financial fields round-trip without changing the calculation value',()=>{
  const h=harness();
  for(const [key,kind,value,display] of [
    ['fixed_cost','currency',35000,'R 35,000.00'],['owner_salary','currency',25000.5,'R 25,000.50'],
    ['expense_percent','percentage',70,'70.00%'],['interest_percent','percentage',12.75,'12.75%'],
    ['population','integer',88113,'88,113'],['start_year','integer',2027,'2027'],
    ['mortality_per_1000','decimal',8.123456,'8.123456'],['volume_growth','percentage',-5,'-5.00%']]){
    const el=h.element(key);el.dataset={input:key,format:kind};h.api.setFieldValue(el,value);
    assert.equal(el.value,display);assert.equal(h.api.fieldNumber(el),value);
  }
  const el=h.element('amount');el.dataset={input:'fixed_cost',format:'currency'};
  for(const [text,value] of [['R 35 000.50',35000.5],['35,000.50',35000.5],['35000,50',35000.5],['0',0],['',null]]){
    el.value=text;assert.equal(h.api.fieldNumber(el),value);
  }
  for(const text of ['R 1,23,000','35garbage','Infinity','1e6','R 1.234,56']){
    el.value=text;assert.throws(()=>h.api.fieldNumber(el),/valid number/);
  }
  el.dataset.format='integer';el.value='60.5';assert.throws(()=>h.api.fieldNumber(el),/whole number/);
});


test('calculation requests send numeric values from formatted controls and reject malformed entries',async()=>{
  const h=harness({},[{dataset:{input:'fixed_cost',format:'currency'},value:'35000.5'},
    {dataset:{input:'expense_percent',format:'percentage'},value:'70'},
    {dataset:{input:'start_year',format:'integer'},value:'2027.0'}]);
  assert.equal(h.element('input-fixed_cost').value,'R 35,000.50');
  assert.equal(h.element('input-start_year').value,'2027');
  const running=h.api.run(false);
  assert.deepEqual(JSON.parse(h.requests[0].options.body).inputs,{salary_in_fixed:false,fixed_cost:35000.5,expense_percent:70,start_year:2027});
  finish(h.requests[0],result);await running;
  h.element('input-fixed_cost').value='R nonsense';await h.api.run(false);
  assert.equal(h.requests.length,1);assert.match(h.element('territory-status').textContent,/valid number/);
});

test('browser dates show full month names and SAST while preserving source period descriptions',()=>{
  const h=harness();
  assert.equal(h.api.dateText('2027-01'),'January 2027');
  assert.equal(h.api.dateText('2011-10-09'),'09 October 2011');
  assert.equal(h.api.dateText('2026-10-21T23:15:00+00:00'),'22 October 2026 at 01:15 SAST');
  assert.equal(h.api.dateText('2026 projection'),'2026 projection');
});


test('map labels avoid collisions and thin context lines at distant zoom',()=>{
  const h=harness();const candidates=[
    {id:'neighbour',name:'Neighbour',kind:'suburb',selection:'context',label_point:[120,100]},
    {id:'selected',name:'Northcliff',kind:'suburb',selection:'include',label_point:[120,100]},
    {id:'other',name:'Fairland',kind:'suburb',selection:'include',label_point:[300,180]},
    {id:'outside',name:'Off screen',kind:'suburb',selection:'include',label_point:[900,900]}];
  const project=([x,y])=>({x,y});
  assert.equal(h.api.labelLayout(candidates,11,project,700,500).length,0);
  assert.deepEqual(Array.from(h.api.labelLayout(candidates,13,project,700,500),a=>a.id),['other','selected']);
  assert.ok(h.api.mapLineStyle(10).weight<h.api.mapLineStyle(15).weight);
  assert.ok(h.api.mapLineStyle(10).opacity<h.api.mapLineStyle(15).opacity);
});

test('removing a distant selected area recomposes and recalculates only the remaining full territory',async()=>{
  const polygon={type:'Polygon',coordinates:[[[27,-26],[27.01,-26],[27.01,-26.01],[27,-26.01],[27,-26]]]};
  const h=harness({boundary_parts:[{id:'old',name:'Westonaria',kind:'town',selection:'include',geometry:polygon},
    {id:'new',name:'Northcliff',kind:'suburb',selection:'include',geometry:polygon}]});
  const old=h.api.getAreas().find(a=>a.id==='old');
  const removing=h.api.changeAreaSelection(old,'context');
  assert.equal(h.requests[0].url,'/compose');
  assert.deepEqual(JSON.parse(h.requests[0].options.body).parts.map(a=>a.name),['Northcliff']);
  finish(h.requests[0],{geometry:polygon});await tick();
  assert.equal(h.requests[1].url,'/calculate');
  const data=JSON.parse(h.requests[1].options.body);
  assert.deepEqual(data.geometry,polygon);assert.equal(data.area_linked_figures,true);
  assert.deepEqual(data.boundary_parts.map(a=>a.name),['Northcliff']);
  finish(h.requests[1],result);await removing;
});


test('a successful new-town search replaces all old selections and local evidence before calculation',async()=>{
  const polygon={type:'Polygon',coordinates:[[[27,-26],[27.01,-26],[27.01,-26.01],[27,-26.01],[27,-26]]]};
  const h=harness({boundary_parts:[{id:'old',name:'Westonaria',kind:'town',selection:'include',geometry:polygon}],sources:[{metric:'Old town',value:'old'}]});
  h.element('boundary-query').value='Randburg';
  const finding=h.api.findAreas(false,false);
  finish(h.requests[0],{areas:[{id:'new',name:'Randburg',kind:'town',province:'Gauteng',geometry:polygon,selection:'context'}],message:'Verified boundary source'});await tick();
  assert.deepEqual(Array.from(h.api.getAreas(),a=>[a.name,a.selection]),[['Randburg','include']]);
  assert.equal(h.element('sources').value,'[]');assert.equal(h.element('research-title').value,'Randburg');
  assert.equal(h.requests[1].url,'/compose');assert.equal(JSON.parse(h.requests[1].options.body).include_enclosed,true);
  finish(h.requests[1],{geometry:polygon});await tick();
  const data=JSON.parse(h.requests[2].options.body);
  assert.deepEqual(data.boundary_parts.map(a=>a.name),['Randburg']);assert.equal(data.include_enclosed,true);
  finish(h.requests[2],result);await finding;
});

test('an unsuccessful town search preserves the current territory',async()=>{
  const h=harness({boundary_parts:[{id:'old',name:'Westonaria',kind:'town',selection:'include'}]});
  h.element('boundary-query').value='No such town';const finding=h.api.findAreas(false,false);
  finish(h.requests[0],{areas:[],message:'No match'});await finding;
  assert.equal(h.api.getAreas()[0].name,'Westonaria');assert.equal(h.api.getAreas()[0].selection,'include');
  assert.equal(h.requests.length,1);
});


test('commercial and fairness cards apply alternative fees without combining them',async()=>{
  const h=harness();const response={...result,commercial_fee:{amount:'150000.00',label:'Standard area',affordable:true,comparison:'below',difference:'250000.00'},profit_margin_fee:{fair_method_version:'v1',fair_fee:'400000.00',total:'400000.00',selected:false,years:[]}};
  const initial=h.api.run(false);finish(h.requests[0],response);await initial;
  assert.match(h.element('commercial-fee-amount').textContent,/R 150,000.00/);
  assert.match(h.element('territory-fair-fee').textContent,/R 400,000.00/);
  assert.match(h.element('commercial-fee-comparison').textContent,/R 250,000.00 below/);
  h.element('apply-commercial-fee').listeners.click();
  assert.equal(h.element('input-fee_method').value,'commercial');assert.equal(h.element('input-territory_fee').value,'150000.00');
  finish(h.requests[1],response);await tick();
  h.element('apply-profit-margin-fee').listeners.click();
  assert.equal(h.element('input-fee_method').value,'fair_territory');assert.equal(h.element('input-territory_fee').value,'400000.00');
  finish(h.requests[2],response);await tick();
});

test('map selection updates reuse neighbouring feature objects instead of rebuilding every outline',()=>{
  const polygon={type:'Polygon',coordinates:[[[27,-26],[28,-26],[28,-27],[27,-27],[27,-26]]]};
  const h=harness({geometry:polygon,boundary_parts:[{id:'a',name:'A',kind:'suburb',selection:'include',geometry:polygon},{id:'b',name:'B',kind:'suburb',selection:'context',geometry:polygon}]});
  const added=[],removed=[];const stub={getProjection:()=>null,data:{addGeoJson(json){const feature={props:{...json.properties},getProperty(key){return this.props[key];},setProperty(key,value){this.props[key]=value;}};added.push(feature);return [feature];},remove(feature){removed.push(feature);}}};
  h.api.installMapForTest(stub);h.api.preview();assert.equal(added.length,3);
  h.api.getAreas()[1].selection='exclude';h.api.preview();
  assert.equal(added.length,3);assert.equal(removed.length,0);assert.equal(added[1].props.selection,'exclude');
});

test('a suburb already inside the combined town is cut out with one click and can be restored',()=>{
  const polygon={type:'Polygon',coordinates:[[[27,-26],[28,-26],[28,-27],[27,-27],[27,-26]]]};
  const h=harness({geometry:polygon});const area={selection:'context',label_point:[27.5,-26.5]};
  assert.equal(h.api.nextAreaSelection(area),'exclude');
  assert.equal(h.api.nextAreaSelection({...area,selection:'exclude'}),'include');
  assert.equal(h.api.nextAreaSelection({...area,label_point:[29,-26.5]}),'include');
});


test('loading a running-concern comparison does not import its proposed inputs',async()=>{
  const h=harness({},[{dataset:{input:'burial_price',format:'currency'},value:'20000'},
    {dataset:{input:'policy_receipts',format:'currency'},value:'0'}]);
  const loading=h.element('load-running-concern').listeners.click();
  finish(h.requests[0],{fingerprint:'comparison-fingerprint',proposed_inputs:{burial_price:999999,policy_receipts:777777}});await tick();
  const body=JSON.parse(h.requests[1].options.body);
  assert.equal(body.inputs.burial_price,20000);assert.equal(body.inputs.policy_receipts,0);
  assert.equal(body.comparison_baseline,'comparison-fingerprint');assert.equal(body.financial_baseline,null);
  finish(h.requests[1],result);await loading;
});

test('prospective suggestions reset holder input values and do not keep a financial baseline',async()=>{
  const h=harness({financial_baseline:'old-holder'},[{dataset:{input:'policy_receipts',format:'currency'},value:'99999'},
    {dataset:{input:'recorded_monthly_services',format:'decimal'},value:'20'}]);
  h.element('research-title').value='Randburg';h.element('prospect-royalty').dataset={format:'percentage',label:'Planning royalty'};
  const starting=h.element('start-prospective').listeners.click();
  finish(h.requests[0],{payload:{inputs:{policy_receipts:0,recorded_monthly_services:0},prospect:{name:'Randburg',royalty_percent:6}},result});await starting;
  const data=h.api.collect();assert.equal(data.scenario_mode,'prospective');assert.equal(data.financial_baseline,null);
  assert.equal(data.comparison_baseline,null);assert.equal(data.inputs.policy_receipts,0);assert.equal(data.inputs.recorded_monthly_services,0);
  assert.equal(data.prospect.royalty_percent,6);assert.equal(data.prospect.name,'Randburg');
});


test('late prospective suggestions cannot overwrite newer edits',async()=>{
  const h=harness({},[{dataset:{input:'policy_receipts',format:'currency'},value:'12345'}]);
  const starting=h.element('start-prospective').listeners.click();h.api.bump();
  finish(h.requests[0],{payload:{inputs:{policy_receipts:0},prospect:{name:'Old area',royalty_percent:6}},result});await starting;
  assert.equal(h.api.collect().inputs.policy_receipts,12345);assert.equal(h.api.collect().scenario_mode,'existing');
  assert.match(h.element('territory-status').textContent,/changed while suggestions loaded/);
});

test('map clicks resolve an already loaded suburb without a publisher request',async()=>{
  const geometry={type:'Polygon',coordinates:[[[27,-27],[28,-27],[28,-26],[27,-26],[27,-27]]]};
  const suburb={id:'loaded-suburb',name:'Loaded suburb',kind:'suburb',selection:'context',geometry,label_point:[27.5,-26.5]};
  const h=harness({boundary_parts:[suburb]});
  const click=h.api.addSuburbAt([27.5,-26.5]);
  assert.equal(h.api.getAreas()[0].selection,'include');
  assert.equal(h.requests.length,1);
  assert.ok(JSON.parse(h.requests[0].options.body).parts,'Only local territory composition is requested');
  finish(h.requests[0],{geometry});await tick();
  assert.equal(h.requests.length,2);
  finish(h.requests[1],result);await click;
  const remove=h.api.addSuburbAt([27.5,-26.5]);
  assert.equal(h.api.getAreas()[0].selection,'exclude');
  assert.equal(h.requests.length,2,'Removing the last selected area does not query the publisher');
  await remove;
});
