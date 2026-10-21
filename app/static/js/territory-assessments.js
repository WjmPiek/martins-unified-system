(() => {
  'use strict';
  const root=document.querySelector('[data-territory-assessments]');
  if(!root)return;
  const keys=['commercial','running','prospective'];
  const labels={commercial:'Martins commercial territory fee',running:'Running concern — recorded business',prospective:'New prospective franchise — suggested territory fee'};
  const details=root.querySelector('#territory-assessment-details');
  const choice=root.querySelector('[data-assessment-print-choice]');
  const print=root.querySelector('[data-print-assessment]');
  const status=root.querySelector('[data-assessment-selection-status]');
  const requested=new URLSearchParams(window.location.search).get('assessment');
  let selected=keys.includes(requested)?requested:'commercial',stale=false;
  function select(key){
    if(!keys.includes(key))return;
    selected=key;choice.value=key;root.dataset.selectedAssessment=key;
    if(window.history?.replaceState){const url=new URL(window.location.href);url.searchParams.set('assessment',key);window.history.replaceState(null,'',url);}
    root.querySelectorAll('[data-assessment-card]').forEach(card=>card.classList.toggle('is-selected',card.dataset.assessmentCard===key));
    root.querySelectorAll('[data-select-assessment]').forEach(tab=>{
      const active=tab.dataset.selectAssessment===key;
      tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
    });
    root.querySelectorAll('[data-assessment-panel]').forEach(panel=>{panel.hidden=stale||panel.dataset.assessmentPanel!==key;});
    const panel=root.querySelector('[data-assessment-panel="'+key+'"]');
    print.disabled=stale||!panel||panel.dataset.available==='false';
    status.textContent=stale?'Inputs changed. Recalculate before reviewing or printing an assessment.':labels[key]+' selected. The details below and printed report use only this assessment.';
  }
  root.addEventListener('click',event=>{
    const tab=event.target.closest('[data-select-assessment]');
    if(tab){select(tab.dataset.selectAssessment);return;}
    const card=event.target.closest('[data-assessment-card]');
    if(card&&!event.target.closest('button,a,input,select,label,textarea'))select(card.dataset.assessmentCard);
  });
  root.addEventListener('keydown',event=>{
    const tab=event.target.closest('[data-select-assessment]');if(!tab)return;
    const offsets={ArrowRight:1,ArrowLeft:-1,Home:0,End:0};if(!(event.key in offsets))return;
    event.preventDefault();const index=keys.indexOf(tab.dataset.selectAssessment);
    const key=event.key==='Home'?keys[0]:event.key==='End'?keys[2]:keys[(index+offsets[event.key]+3)%3];
    select(key);root.querySelector('[data-select-assessment="'+key+'"]').focus();
  });
  choice.addEventListener('change',()=>select(choice.value));
  print.addEventListener('click',()=>{select(choice.value);if(!print.disabled)window.print();});
  window.TerritoryAssessments={
    refresh(html,assessments){
      if(typeof html!=='string')return;
      // Only server-rendered, escaped templates from the authenticated calculation response.
      details.innerHTML=html;stale=false;
      Object.entries(assessments||{}).forEach(([key,assessment])=>{
        const card=root.querySelector('[data-assessment-card="'+key+'"]');
        const amount=card?.querySelector('.territory-fee-amount');
        if(amount)amount.textContent=assessment.fee==null?'Unavailable':'R '+new Intl.NumberFormat('en-GB',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(assessment.fee));
      });
      select(selected);
    },
    invalidate(){stale=true;select(selected);},
    select
  };
  select(selected);
})();
