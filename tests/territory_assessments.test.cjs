const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function harness(query=''){
  const keys=['commercial','running','prospective'],listeners={},controls={};let prints=0;
  const el=()=>({dataset:{},attributes:{},hidden:false,classList:{toggle(name,value){this[name]=value;}},setAttribute(k,v){this.attributes[k]=v;},focus(){this.focused=true;},addEventListener(k,fn){this[k]=fn;}});
  const cards=keys.map(key=>{const e=el();e.dataset.assessmentCard=key;e.amount=el();e.querySelector=()=>e.amount;return e;});
  const tabs=keys.map(key=>{const e=el();e.dataset.selectAssessment=key;e.closest=selector=>selector==='[data-select-assessment]'?e:null;return e;});
  const panels=keys.map(key=>{const e=el();e.dataset.assessmentPanel=key;return e;});
  for(const key of ['#territory-assessment-details','[data-assessment-print-choice]','[data-print-assessment]','[data-assessment-selection-status]'])controls[key]=el();
  const root=el();root.addEventListener=(k,fn)=>listeners[k]=fn;
  root.querySelectorAll=selector=>selector==='[data-assessment-card]'?cards:selector==='[data-select-assessment]'?tabs:panels;
  root.querySelector=selector=>controls[selector]||(/data-assessment-panel=/.test(selector)?panels:/data-select-assessment=/.test(selector)?tabs:cards)[keys.indexOf(selector.match(/="(.*?)"/)[1])];
  const window={location:{search:query},print:()=>prints++};
  vm.runInNewContext(fs.readFileSync('app/static/js/territory-assessments.js','utf8'),{document:{querySelector:()=>root},window,URLSearchParams,Intl});
  return {api:window.TerritoryAssessments,root,cards,tabs,panels,controls,listeners,prints:()=>prints};
}
test('selecting each card shows only its own details and synchronises print choice',()=>{
  const h=harness();
  for(const key of ['running','prospective','commercial']){
    const tab=h.tabs.find(e=>e.dataset.selectAssessment===key);h.listeners.click({target:tab});
    assert.deepEqual(h.panels.filter(e=>!e.hidden).map(e=>e.dataset.assessmentPanel),[key]);
    assert.equal(h.controls['[data-assessment-print-choice]'].value,key);
    assert.equal(tab.attributes['aria-selected'],'true');
  }
});
test('print chooser switches assessment; stale or unavailable scenarios cannot print',()=>{
  const h=harness('?assessment=running'),choice=h.controls['[data-assessment-print-choice]'],button=h.controls['[data-print-assessment]'];
  assert.equal(h.root.dataset.selectedAssessment,'running');button.click();assert.equal(h.prints(),1);
  h.api.invalidate();assert.ok(h.panels.every(p=>p.hidden));button.click();assert.equal(h.prints(),1);
  h.api.refresh('server-rendered content',{});assert.equal(h.root.dataset.selectedAssessment,'running');
  h.panels[1].dataset.available='false';h.api.select('running');assert.equal(button.disabled,true);
  choice.value='prospective';choice.change();button.click();assert.equal(h.prints(),2);
  assert.equal(h.panels[2].hidden,false);assert.equal(h.panels[1].hidden,true);
});
test('keyboard navigation and refreshed results preserve the selected assessment',()=>{
  const h=harness('?assessment=invalid');
  h.listeners.keydown({target:h.tabs[0],key:'ArrowRight',preventDefault(){}});
  assert.equal(h.root.dataset.selectedAssessment,'running');assert.equal(h.tabs[1].focused,true);
  h.api.refresh('new escaped template',{running:{fee:12345}});
  assert.equal(h.cards[1].amount.textContent,'R 12,345.00');assert.equal(h.panels[1].hidden,false);
});
