import {$,make} from './ui.js';

export function setupDeparture(request,demo,onSelect){
  const input=$('origin'), id=$('departure'), list=$('departure-options'), mode=$('travel-mode');
  let generation=0, directory, timer, rows=[], active=-1;
  function close(){
    list.hidden=true;input.setAttribute('aria-expanded','false');
    input.removeAttribute('aria-activedescendant');active=-1;
  }
  function choose(row){
    generation++;clearTimeout(timer);
    input.value=row.name;id.value=row.id;input.setCustomValidity('');close();onSelect();
  }
  async function search(){
    const version=++generation,q=input.value.trim(), travelMode=mode.value;
    try{
      if(demo){
        directory ||= fetch('/static/travel/data/departures.json').then(r=>r.json());
        const kind={high_speed:'station',rail:'station',air:'airport',car:'city',coach:'city',tour:'city'}[travelMode];
        const items=(await directory).items;
        if(version!==generation)return;
        rows=items.filter(r=>r.kind===kind && [r.name,r.city,r.id,r.province].some(s=>s.toLowerCase().includes(q.toLowerCase()))).slice(0,80);
      }else{
        const data=await request('/api/travel/departures?'+new URLSearchParams({mode:travelMode,q}));
        if(version!==generation)return;
        rows=data.items;
      }
      list.replaceChildren();
      rows.forEach((r,i)=>{
        const el=make('div',null,'departure-choice');el.id='place-option-'+i;
        el.setAttribute('role','option');el.setAttribute('aria-selected','false');
        el.append(make('strong',r.name),make('small',[r.city,r.province,r.kind==='airport'?r.id.slice(4):''].filter(Boolean).join(' · ')));
        el.onmousedown=e=>e.preventDefault();el.onclick=()=>choose(r);
        list.append(el);
      });
      if(!rows.length)list.append(make('small','未找到匹配地点，请换个名称。'));
    }catch{
      if(version!==generation)return;
      rows=[];list.replaceChildren(make('small','目录暂不可用，重新输入可再查。'));
    }
    if(document.activeElement===input){list.hidden=false;input.setAttribute('aria-expanded','true');}
  }
  input.onfocus=search;
  input.oninput=()=>{generation++;rows=[];id.value='';input.setCustomValidity('');close();clearTimeout(timer);timer=setTimeout(search,120);};
  input.onblur=()=>{close();};
  input.onkeydown=e=>{
    if(e.isComposing)return;
    if(e.key==='Escape'){close();return;}
    if(e.key==='Enter' && !list.hidden && rows.length){
      e.preventDefault();choose(rows[active<0?0:active]);return;
    }
    if(!['ArrowDown','ArrowUp'].includes(e.key))return;
    e.preventDefault();
    if(list.hidden){void search();return;}
    if(!rows.length)return;
    active=Math.max(0,Math.min(rows.length-1,active+(e.key==='ArrowDown'?1:-1)));
    const option=list.children[active];
    for(const el of list.children)el.setAttribute('aria-selected',String(el===option));
    input.setAttribute('aria-activedescendant',option.id);option.scrollIntoView({block:'nearest'});
  };
  mode.addEventListener('change',()=>{
    input.value=id.value='';generation++;clearTimeout(timer);close();
    input.placeholder=mode.value==='air'?'输入城市、机场或三字码':['rail','high_speed'].includes(mode.value)?'输入城市或车站名称':'输入出发城市';
    input.focus();
  });
  if(document.activeElement===input)void search();
  return {choose,confirmed(){
    if(id.value)return true;
    const exact=rows.filter(r=>r.name===input.value.trim());
    if(exact.length===1){choose(exact[0]);return true;}
    input.focus();input.setCustomValidity('请从候选目录中选择出发地。');input.reportValidity();return false;
  }};
}
