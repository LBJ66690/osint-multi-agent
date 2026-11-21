import {make, button} from './ui.js';

function exportText(body, name) {
  const url = URL.createObjectURL(new Blob(['\uFEFF', body], {type:'text/plain;charset=utf-8'}));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name + '.txt';
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function renderFormat(root, detail) {
  if (!detail.card_report || detail.rendered_format !== 'cards') return;
  const title = root.querySelector('h1').textContent;
  const hero = root.firstElementChild;
  const evidence = make('details',null,'report-evidence');
  evidence.append(make('summary','交通、天气、预约及费用依据'), ...Array.from(root.children).slice(1));
  root.replaceChildren(hero);
  const card = detail.card_report;
  const labels=['怎么到','怎么玩','怎么选','同行卡'];
  const scenes = card.scenes.map((scene,i)=>{
    const box=make('details',null,'journey-card scene');
    box.open=true;
    box.append(make('summary',labels[i]),make('h2',scene.title));
    const nodes=make('ol',null,'route-stops');
    const tip=make('p');
    box.append(nodes,tip);
    root.append(box);
    return {box,nodes,tip};
  });
  let selected=null;
  const actions=make('div',null,'actions');
  scenes[2].box.append(actions);
  const brief=make('p');brief.setAttribute('aria-live','polite');
  scenes[3].box.append(brief);
  function current(i){return i===2 && selected ? selected : card.scenes[i];}
  function paint(){
    scenes.forEach(({nodes,tip},i)=>{
      nodes.replaceChildren(...current(i).nodes.map(n=>make('li',n)));
      tip.textContent=current(i).tip;
    });
    brief.textContent=selected?selected.summary:(card.choices.length?'同行选择待确定':card.pending[0]||'按行程核对出发事项');
    Array.from(actions.children).forEach((el,i)=>el.setAttribute('aria-pressed',String(selected===card.choices[i])));
  }
  for(const choice of card.choices)actions.append(button(choice.label,()=>{selected=choice;paint();}));
  const prices=card.budget.map(([label,amount])=>label+' · '+(amount===null?'待报价':amount+' 元'));
  const list=make('ul');list.append(...prices.map(t=>make('li',t)));
  const pending=make('ul');pending.append(...card.pending.map(t=>make('li',t)));
  scenes[3].box.append(make('h3','行程费用'),list,make('h3','出发前核对'),pending,button('保存同行文本',()=>{
    const text=[title,hero.innerText,...scenes.map((_,i)=>[labels[i],card.scenes[i].title,current(i).nodes.join(' → '),current(i).tip].join('\n')),brief.textContent,...prices,...card.pending].join('\n\n');
    exportText(text,title);
  }));
  paint();
  root.append(evidence);
}
