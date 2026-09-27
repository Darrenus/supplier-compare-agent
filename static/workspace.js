(() => {
  'use strict';
  const DIMS = [['price','Price',3],['lead_time','Lead time',2],['payment_terms','Payment terms',1],['on_time_delivery_rate','On-time delivery',2],['quality_rating','Quality',2]];
  const $ = id => document.getElementById(id);
  // Supplier-provided strings are always text nodes, never HTML.
  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key,value] of Object.entries(props || {})) {
      if (key === 'class') node.className = value;
      else if (key === 'style') node.setAttribute(key,value);
      else node[key] = value;
    }
    for (const child of children.flat(Infinity)) if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return node;
  }
  const money = value => `SGD ${Number(value).toFixed(2)}`;
  const percent = value => `${Number((value * 100).toFixed(1))}%`;
  function bar(value) {return el('span',{class:'bartrack',style:'width:58px'},el('span',{class:'bar',style:`width:${Math.max(0,Math.min(1,value))*100}%`}));}
  function syncTheme() {
    const dark = document.documentElement.dataset.theme === 'dark';
    $('theme-toggle').replaceChildren(el('span',{},dark?'☀':'◐'), ' ', el('span',{},dark?'Light':'Dark'));
    $('theme-toggle').setAttribute('aria-label',`Switch to ${dark?'light':'dark'} theme`);
  }
  syncTheme();
  $('theme-toggle').addEventListener('click',()=>{
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    try {localStorage.setItem('supplier-theme',theme);} catch (_) {}
    syncTheme();
  });
  function syncWeights() {
    const sum = DIMS.reduce((total,[key])=>total+Number($('w_'+key).value),0);
    for (const [key] of DIMS) {
      const input=$('w_'+key), value=sum?Math.round(Number(input.value)/sum*100):0;
      $('o_'+key).value=`${value}%`;
      input.setAttribute('aria-valuetext',`${value}% relative weight`);
      input.style.setProperty('--fill', `${Number(input.value)*10}%`);
    }
  }
  for (const [key,label,value] of DIMS) {
    const input=el('input',{type:'range',min:0,max:10,step:1,value,id:'w_'+key});
    input.addEventListener('input',syncWeights);
    $('weights').append(el('div',{},el('label',{htmlFor:'w_'+key},label,el('output',{id:'o_'+key})),input));
  }
  syncWeights();
  $('reset-weights').addEventListener('click',()=>{for(const [key,,value] of DIMS)$('w_'+key).value=value;syncWeights();markDirty();});
  let busy=false, revision=0;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  function markDirty(){revision++;if(!busy && $('out').querySelector('.product-context'))setStatus('','Settings changed. Compare suppliers to update these results.');}
  $('form').addEventListener('input',markDirty);
  $('sku').addEventListener('change',()=>{markDirty();});
  function readRequest(){
    const body={sku:$('sku').value,weights:{}};
    for(const [key] of DIMS)body.weights[key]=Number($('w_'+key).value);
    if($('quantity').value)body.quantity=Number($('quantity').value);
    if($('max_lead').value)body.max_lead_time_days=Number($('max_lead').value);
    return body;
  }
  function setStatus(kind,text){$('status').className='status '+kind; $('status').textContent=text; $('status').classList.toggle('hidden',!text);}
  function animateResult(node) {
    if (!node || reducedMotion.matches) return;
    node.animate([{opacity:0,transform:'translateY(7px)'},{opacity:1,transform:'translateY(0)'}],{duration:420,easing:'cubic-bezier(.2,.7,.2,1)'});
  }
  function generationState(state) {
    const label = state === 'pending' ? 'Preparing your recommendation' : state === 'complete' ? 'Recommendation ready' : 'Comparison ready';
    const node = el('div',{class:'generation-state '+state},el('span',{class:'generation-icon'},state==='pending'?'': '✓'),el('div',{},el('strong',{},label),el('span',{class:'generation-caption'},state==='pending'?'You can explore the scores while we review the quotes.':state==='complete'?'Review the recommendation and negotiation opportunities below.':'Rule-based guidance is available below.')));
    node.setAttribute('role','status');
    if(state==='pending') {
      const shimmer=el('span',{class:'generation-track'},el('span',{class:'generation-sweep'}));
      shimmer.setAttribute('aria-hidden','true');node.append(shimmer);
    }
    return node;
  }
  function sourceBadge(source){return el('span',{class:'badge'},({llm:'AI-generated explanation',offline:'Rule-based explanation',fallback:'AI unavailable · rule-based explanation'})[source] || 'Computed ranking');}
  function renderRecommendation(compare,agent,pending){
    const top=compare.ranked[0];
    if(!top)return el('section',{class:'empty-state'},el('span',{class:'empty-mark'},'∅'),el('h2',{},'No matching suppliers'),el('p',{},'Try increasing the order quantity or allowing a longer lead time.'));
    const rec=agent?.recommendation;
    const leverData=(compare.negotiation_levers || []).find(x=>x.supplier_id===top.supplier_id)?.levers || [];
    const points=rec?.negotiation_points || leverData.map(x=>x.text);
    const metrics=[ [money(top.raw.unit_price),'Unit price'],[`${top.raw.lead_time_days} days`,'Lead time'],[percent(top.raw.on_time_delivery_rate),'On-time delivery'],[`${top.raw.quality_rating} / 5`,'Quality'] ];
    const explanation=el('details',{class:'explanation'},el('summary',{},'Why this recommendation?',sourceBadge(agent?.source)),
      el('p',{},rec?.rationale || 'The highest weighted score among eligible suppliers, calculated from your selected priorities.'),
      points.length>2?el('div',{},el('h3',{},'More negotiation opportunities'),el('ul',{},points.slice(2).map(p=>el('li',{},p)))):null,
      rec?.risks?.length?el('div',{},el('h3',{},'Risks to consider'),el('ul',{},rec.risks.map(r=>el('li',{},r)))):null,
      agent?el('div',{class:'meta'},`Request ${agent.request_id} · LLM calls ${agent.usage?.llm_calls ?? 0} · Tokens ${agent.usage?.input_tokens ?? 0} in / ${agent.usage?.output_tokens ?? 0} out`):null);
    return el('section',{class:'rec'},
      el('div',{class:'rec-heading'},el('div',{},el('p',{class:'eyebrow'},'★  Recommended supplier'),el('h2',{class:'who'},top.supplier),el('p',{class:'rec-subtitle'},'Highest weighted score across your priorities.')),
        el('div',{class:'hero-score'},el('strong',{},top.score.toFixed(3)),el('span',{},'Weighted score'))),
      el('div',{class:'metrics'},metrics.map(([value,label])=>el('div',{class:'metric'},el('strong',{},value),el('span',{},label)))),
      el('div',{class:'levers-heading'},'◇  Negotiation opportunities'),
      points.length?el('div',{class:'levers'},points.slice(0,2).map(p=>el('div',{class:'lever'},p))):el('p',{class:'small'},'No benchmark gaps identified for this comparison.'),
      pending?generationState('pending'):agent?generationState(agent.source==='llm'?'complete':'fallback'):null,explanation);
  }
  function renderTable(compare){
    const rows=compare.ranked.map((r,i)=>{
      const why=el('details',{id:'why-'+r.supplier_id,class:'why-details'},el('summary',{class:'why-toggle'},'Why this supplier?',el('span',{class:'why-chevron','aria-hidden':'true'},'▸')),el('div',{class:'why'},DIMS.map(([key,label])=>[
        el('span',{class:'dim'},`${label} · ${Math.round(compare.weights[key]*100)}%`),el('span',{},`${r.breakdown[key].toFixed(2)} → +${r.weighted[key].toFixed(3)}`)
      ])));
      return el('tr',{class:i===0?'top':''},el('td',{},i+1),el('td',{},el('div',{class:'supplier-name'},r.supplier),el('div',{class:'supplier-id'},r.supplier_id,r.injection_flag?el('span',{class:'badge inj'},'Flagged'):null),why),
        el('td',{class:'num'},r.score.toFixed(3),bar(r.score)),el('td',{class:'num'},money(r.raw.unit_price)),el('td',{class:'num'},`${r.raw.lead_time_days}d`),el('td',{class:'num'},r.raw.payment_terms),el('td',{class:'num'},r.raw.moq),el('td',{class:'num'},percent(r.raw.on_time_delivery_rate)),el('td',{class:'num'},r.raw.quality_rating));
    });
    const table=el('table',{},el('thead',{},el('tr',{},['Rank','Supplier','Score','Unit price','Lead time','Terms','MOQ','On-time','Quality'].map((h,i)=>el('th',{scope:'col',class:i>=2?'num':''},h)))),el('tbody',{},rows));
    const wrap=el('div',{class:'table-wrap',tabIndex:0},table);wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Supplier comparison table; scroll horizontally on small screens');
    return el('section',{},el('div',{class:'comparison-heading'},el('div',{},el('h2',{},'Supplier comparison'),el('p',{},'Ranked by your weighted priorities')),el('span',{class:'count-label'},`${rows.length} eligible quotes`)),rows.length?wrap:null,
      compare.excluded.length?el('div',{class:'excluded'},el('h2',{},`${compare.excluded.length} suppliers excluded by your constraints`),el('ul',{},compare.excluded.map(e=>el('li',{},el('strong',{},e.supplier),': ',e.reasons.join('; '))))):null);
  }
  function renderInjection(compare,agent){
    const ids=compare.injection_suppliers || [];if(!ids.length)return null;
    const names=Object.fromEntries([...compare.ranked,...compare.excluded].map(r=>[r.supplier_id,r.supplier]));
    return el('details',{class:'flag'},el('summary',{},`⚠  ${ids.length} supplier descriptions flagged · View details`),el('ul',{},ids.map(id=>el('li',{},`${names[id] || id} (${id})`,agent?.injection_details?.[id]?el('div',{},el('code',{},agent.injection_details[id].join(' · '))):null))));
  }
  function render(compare,agent,pending=false){
    const context=el('section',{class:'product-context'},el('h2',{},`${compare.sku} / ${compare.product?.name || 'Supplier quotes'}`),el('p',{},`${compare.ranked.length+compare.excluded.length} supplier quotes · SGD${compare.constraints?.quantity?' · Quantity '+compare.constraints.quantity:''}${compare.constraints?.max_lead_time_days?' · Maximum '+compare.constraints.max_lead_time_days+' days':''}`));
    $('out').replaceChildren(...[context,renderRecommendation(compare,agent,pending),renderTable(compare),renderInjection(compare,agent)].filter(Boolean));
    animateResult($('out').querySelector('.rec'));
    if(pending)$('out').querySelector('.rec')?.classList.add('is-generating');
  }

  function finishRecommendation(compare,agent,failed=false) {
    const current=$('out').querySelector('.rec');
    if(!current) {render(compare,agent);return;}
    // Preserve table focus, expanded score details, and scroll position.
    const explanationOpen=current.querySelector('.explanation')?.open;
    const next=renderRecommendation(compare,agent,false);
    if(failed)next.append(generationState('fallback'));
    next.classList.add('just-completed');
    if(explanationOpen)next.querySelector('.explanation').open=true;
    const anchor=$('out').querySelector('.table-wrap');
    const oldTop=anchor?.getBoundingClientRect().top;
    current.replaceWith(next);
    if(oldTop!==undefined && oldTop<0)window.scrollBy(0,anchor.getBoundingClientRect().top-oldTop);
    animateResult(next);
    const flag=$('out').querySelector('.flag');
    if(flag){const updated=renderInjection(compare,agent);if(updated){updated.open=flag.open;flag.replaceWith(updated);}}
  }
  $('out').addEventListener('toggle', event => {
    if(event.target.open && !reducedMotion.matches) {
      for(const child of event.target.children)if(child.tagName!=='SUMMARY')child.animate([{opacity:0,transform:'translateY(-3px)'},{opacity:1,transform:'translateY(0)'}],{duration:200,easing:'ease-out'});
    }
  },true);
  async function post(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok&&!data.compare)throw new Error(data.error || `HTTP ${response.status}`);return {response,data};}
  async function compareRequest(withAgent){
    if(busy)return;
    const body=readRequest(), requestRevision=revision;
    if(!Object.values(body.weights).some(x=>x>0)){setStatus('error','Set at least one decision priority above zero.');return;}
    busy=true;$('go').disabled=true;$('go').replaceChildren(el('span',{class:'button-spinner'}),'Comparing…');$('out').setAttribute('aria-busy','true');
    $('server-result')?.remove();setStatus('loading','Comparing supplier quotes…');
    let computed=false, comparison=null;
    try{
      const {data}=await post('/api/compare',body);render(data,null,withAgent&&data.ranked.length>0);computed=true;comparison=data;
      if(withAgent&&data.ranked.length){
        setStatus('','');
        $('go').replaceChildren(el('span',{class:'button-spinner'}),'Preparing recommendation');
        const {response,data:result}=await post('/api/recommend',body);finishRecommendation(result.compare,result.agent,!response.ok);
        setStatus(response.ok?'':'error',response.ok?'':`AI explanation unavailable. The computed comparison is still available. ${result.error || ''}`);
      }else setStatus('','');
      if(revision!==requestRevision)setStatus('','Settings changed during comparison. Compare again to apply your latest settings.');
    }catch(error){if(comparison)finishRecommendation(comparison,null,true);setStatus('error',`${computed?'The ranking is ready, but the AI explanation could not be loaded.':'Could not load the comparison.'} ${error.message} Please try again.`);}
    finally{busy=false;$('go').disabled=false;$('go').replaceChildren('Compare suppliers ',el('span',{},'→'));$('out').setAttribute('aria-busy','false');}
  }
  $('form').addEventListener('submit',event=>{event.preventDefault();compareRequest(true);});
  // Show real deterministic results on arrival, without spending LLM tokens.
  if(!$('server-result'))compareRequest(false);
})();
