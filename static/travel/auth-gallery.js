import {loadCatalog} from './pois.js';
const image=document.getElementById('auth-photo');
const play=document.getElementById('photo-play');
const motion=matchMedia('(prefers-reduced-motion: reduce)');
let playing=!motion.matches, index=0, timer;
loadCatalog().then(catalog=>{
  const slides=[...catalog.values()].filter(p=>!p.parent_id && !p.street_id && p.images?.length);
  if(!slides.length)return;
  const gallery=document.getElementById('travel-pictures');
  const buttons=slides.map((poi,i)=>{
    const b=document.createElement('button');b.type='button';b.textContent=poi.name;
    b.onclick=()=>{index=i;paint();};
    document.getElementById('photo-places').append(b);
    return b;
  });
  const full=document.getElementById('photo-full');
  full.hidden=!document.fullscreenEnabled;
  full.onclick=async()=>{
    try{if(document.fullscreenElement)await document.exitFullscreen();else await gallery.requestFullscreen();}
    catch{full.hidden=true;}
  };
  document.addEventListener('fullscreenchange',()=>{full.textContent=document.fullscreenElement?'退出全屏':'全屏赏景';});
  motion.addEventListener('change',()=>{playing=!motion.matches;paint();});
  function paint(){
    const poi=slides[index], photo=poi.images[0], credit=document.getElementById('photo-credit');
    image.src=photo.src;image.alt=photo.alt||poi.name;
    document.getElementById('auth-place').textContent=poi.name;
    credit.href=photo.source_url;credit.textContent=photo.credit+' · '+photo.license;
    clearTimeout(timer);
    image.classList.remove('moving');void image.offsetWidth;image.classList.toggle('moving',playing);
    gallery.classList.toggle('touring',playing);
    buttons.forEach((b,i)=>b.setAttribute('aria-pressed',String(i===index)));
    play.textContent=playing?'暂停巡游':'自动巡游';
    if(playing && !document.hidden)timer=setTimeout(()=>move(1),8000);
  }
  function move(delta){index=(index+delta+slides.length)%slides.length;paint();}
  document.getElementById('photo-prev').onclick=()=>move(-1);
  document.getElementById('photo-next').onclick=()=>move(1);
  play.onclick=()=>{playing=!playing;paint();};
  document.addEventListener('visibilitychange',paint);
  paint();
}).catch(()=>{document.querySelector('.travel-pictures figure').hidden=true;document.querySelector('.picture-controls').hidden=true;});
