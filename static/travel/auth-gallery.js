const image=document.getElementById('auth-photo');
const play=document.getElementById('photo-play');
const motion=matchMedia('(prefers-reduced-motion: reduce)');
let playing=!motion.matches, index=0, timer;
const slides = [
  {
    "name": "花江峡谷大桥",
    "verse": "飞虹横渡北盘江，云雾出岫绕千峰。",
    "area": "安顺关岭与黔西南贞丰交界"
  },
  {
    "name": "黄果树大瀑布",
    "verse": "捣珠崩玉，飞沫反涌，如烟雾腾空。",
    "area": "安顺镇宁"
  },
  {
    "name": "兴义万峰林",
    "verse": "天下山峰何其多，唯有此处峰成林。",
    "area": "黔西南兴义"
  },
  {
    "name": "荔波小七孔",
    "verse": "响水河畔青石拱，一潭碧玉映翠微。",
    "area": "黔南荔波"
  }
];
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
  const poi=slides[index], credit=document.getElementById('photo-credit');
  image.src='/static/travel/assets/scroll/'+(index+1)+'.jpg';image.alt=poi.name;
  document.getElementById('auth-place').textContent=poi.name;
  credit.textContent=poi.area+' · '+poi.verse;
  clearTimeout(timer);
  image.classList.remove('moving');void image.offsetWidth;image.classList.toggle('moving',playing);
  gallery.dataset.scene=['cloud','falls','cloud','river'][index];
  gallery.classList.toggle('touring',playing);
  buttons.forEach((b,i)=>b.setAttribute('aria-pressed',String(i===index)));
  play.textContent=playing?'暂停巡游':'自动巡游';
  if(playing && !document.hidden)timer=setTimeout(()=>move(1),3000);
}
function move(delta){index=(index+delta+slides.length)%slides.length;paint();}
document.getElementById('photo-prev').onclick=()=>move(-1);
document.getElementById('photo-next').onclick=()=>move(1);
play.onclick=()=>{playing=!playing;paint();};
document.addEventListener('visibilitychange',paint);
paint();
