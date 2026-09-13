(() => {
  const API=(window.SIDERAL_API_BASE||'https://sideral-backend.onrender.com').replace(/\/$/,'');
  const ENDPOINT=`${API}/api/estacoes/sideral`;
  const panel=document.getElementById('stationControls'), toggle=document.getElementById('stationControlsToggle');
  let all=[], filtered=[], visible=[], meta=null, serial=0, moveTimer, searchTimer;
  const layer=L.layerGroup();
  const map=L.map('map',{zoomControl:true,minZoom:3,maxZoom:18,preferCanvas:true}).setView([-15.5,-52.5],4);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'&copy; OpenStreetMap contributors',maxZoom:19}).addTo(map); layer.addTo(map);

  const cfg={
    temperature:{label:'Temperatura',unit:'°C',dec:1,r:[[5,'#2563eb','≤ 5'],[15,'#06b6d4','6–14'],[25,'#22c55e','15–24'],[32,'#eab308','25–31'],[38,'#f97316','32–37'],[Infinity,'#dc2626','≥ 38']]},
    humidity:{label:'Umidade',unit:'%',dec:0,r:[[30,'#f97316','< 30'],[45,'#facc15','30–44'],[60,'#a3e635','45–59'],[75,'#22c55e','60–74'],[90,'#06b6d4','75–89'],[Infinity,'#2563eb','≥ 90']]},
    windSpeed:{label:'Vento',unit:'km/h',dec:0,r:[[10,'#22c55e','< 10'],[20,'#84cc16','10–19'],[30,'#eab308','20–29'],[45,'#f97316','30–44'],[60,'#ef4444','45–59'],[Infinity,'#7e22ce','≥ 60']]},
    windGust:{label:'Rajada',unit:'km/h',dec:0,r:[[20,'#22c55e','< 20'],[35,'#84cc16','20–34'],[50,'#eab308','35–49'],[65,'#f97316','50–64'],[85,'#ef4444','65–84'],[Infinity,'#7e22ce','≥ 85']]},
    rain24h:{label:'Chuva 24 h',unit:'mm',dec:1,r:[[.1,'#e2e8f0','0'],[5,'#86efac','0,1–4,9'],[15,'#22c55e','5–14,9'],[30,'#06b6d4','15–29,9'],[60,'#2563eb','30–59,9'],[100,'#7e22ce','60–99,9'],[Infinity,'#be123c','≥ 100']]},
    rain1h:{label:'Chuva 1 h',unit:'mm',dec:1,r:[[.1,'#e2e8f0','0'],[2.5,'#86efac','0,1–2,4'],[10,'#22c55e','2,5–9,9'],[25,'#06b6d4','10–24,9'],[50,'#2563eb','25–49,9'],[Infinity,'#7e22ce','≥ 50']]},
    pressure:{label:'Pressão',unit:'hPa',dec:0,r:[[980,'#7e22ce','< 980'],[995,'#2563eb','980–994'],[1005,'#06b6d4','995–1004'],[1015,'#22c55e','1005–1014'],[1025,'#eab308','1015–1024'],[Infinity,'#f97316','≥ 1025']]},
    riverLevel:{label:'Nível de rio',unit:'m',dec:2,r:[[1,'#22c55e','< 1'],[2,'#84cc16','1–1,99'],[3,'#eab308','2–2,99'],[5,'#f97316','3–4,99'],[Infinity,'#ef4444','≥ 5']]}
  };
  const labels={INMET:'INMET',CEMADEN:'CEMADEN Pluviômetros','CEMADEN-HIDRO':'CEMADEN Hidrologia',DCRS:'Defesa Civil RS',METAR:'METAR / NOAA'};
  const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const num=v=>Number.isFinite(Number(v))?Number(v):null;
  const fmt=(v,d=0)=>num(v)===null?'—':num(v).toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d});
  const variable=()=>document.getElementById('variableFilter').value;
  const source=n=>labels[n]||n;
  const fresh=s=>Number.isFinite(Number(s.ageMinutes))&&Number(s.ageMinutes)<=120;

  function openPanel(open){panel.classList.toggle('mobile-open',open);toggle.setAttribute('aria-expanded',String(open));const i=toggle.querySelector('i');if(i)i.className=open?'ph ph-x':'ph ph-sliders-horizontal'}
  toggle.addEventListener('click',()=>openPanel(!panel.classList.contains('mobile-open')));
  map.on('click',()=>{if(matchMedia('(max-width:800px)').matches)openPanel(false)});

  function color(v,val){const n=num(val);if(n===null)return'#94a3b8';for(const [limit,c] of cfg[v].r)if(n<limit||limit===Infinity)return c;return'#94a3b8'}
  function textColor(hex){const c=hex.slice(1),r=parseInt(c.slice(0,2),16),g=parseInt(c.slice(2,4),16),b=parseInt(c.slice(4,6),16);return(.299*r+.587*g+.114*b)/255>.64?'#0f172a':'#fff'}
  function legend(){const v=variable(),c=cfg[v];document.getElementById('legendTitle').textContent=`${c.label} · observações reais`;document.getElementById('legendRows').innerHTML=c.r.map(([,co,l])=>`<div class="legend-row"><span class="legend-dot" style="background:${co}"></span>${esc(l)} ${esc(c.unit)}</div>`).join('')}

  function filters(){
    const ns=document.getElementById('networkFilter'), us=document.getElementById('ufFilter'), n0=ns.value||'TODAS',u0=us.value||'TODAS';
    const nets=[...new Set(all.map(x=>x.network).filter(Boolean))].sort(),ufs=[...new Set(all.map(x=>x.uf).filter(Boolean))].sort();
    ns.innerHTML='<option value="TODAS">Todas as redes</option>'+nets.map(n=>`<option value="${esc(n)}">${esc(source(n))} (${meta?.counts?.[n]??0})</option>`).join('');ns.value=nets.includes(n0)?n0:'TODAS';
    us.innerHTML='<option value="TODAS">Brasil</option>'+ufs.map(u=>`<option value="${esc(u)}">${esc(u)}</option>`).join('');us.value=ufs.includes(u0)?u0:'TODAS';
    document.getElementById('sourceSummary').innerHTML=Object.entries(meta?.counts||{}).sort((a,b)=>b[1]-a[1]).map(([n,c])=>`<span class="source-chip"><b>${c.toLocaleString('pt-BR')}</b> ${esc(source(n))}</span>`).join('');
  }

  function apply(){
    const n=document.getElementById('networkFilter').value,u=document.getElementById('ufFilter').value,f=document.getElementById('freshnessFilter').value,q=document.getElementById('stationSearch').value.trim().toLocaleLowerCase('pt-BR');
    filtered=all.filter(s=>{
      if(n!=='TODAS'&&s.network!==n)return false;if(u!=='TODAS'&&s.uf!==u)return false;
      if(f==='RECENTES'&&!fresh(s))return false;if(f==='ATRASADAS'&&!(Number.isFinite(Number(s.ageMinutes))&&Number(s.ageMinutes)>120))return false;if(f==='SEM_DADO'&&Number.isFinite(Number(s.ageMinutes)))return false;
      if(q&&!([s.code,s.name,s.city,s.uf,s.network].filter(Boolean).join(' ').toLocaleLowerCase('pt-BR').includes(q)))return false;return true;
    });draw();
  }

  function limit(){const z=map.getZoom();return z<=4?360:z===5?700:z===6?1200:z===7?2200:z===8?3500:6000}
  function pick(rows){
    const b=map.getBounds().pad(.15), candidates=rows.filter(s=>num(s.lat)!==null&&num(s.lon)!==null&&b.contains([s.lat,s.lon])),max=limit();if(candidates.length<=max)return candidates;
    const v=variable(),size=map.getSize(),cols=Math.max(20,Math.ceil(Math.sqrt(max*Math.max(1,size.x/Math.max(1,size.y))))),rws=Math.max(14,Math.ceil(max/cols));let selected=0;const cells=new Map(),w=b.getWest(),e=b.getEast(),so=b.getSouth(),no=b.getNorth();
    candidates.sort((a,z)=>(num(z[v])!==null)-(num(a[v])!==null)||(fresh(z)?1:0)-(fresh(a)?1:0));
    for(const s of candidates){let x=Math.max(0,Math.min(cols-1,Math.floor((s.lon-w)/Math.max(1e-9,e-w)*cols))),y=Math.max(0,Math.min(rws-1,Math.floor((s.lat-so)/Math.max(1e-9,no-so)*rws)));const k=`${x}:${y}`,bucket=cells.get(k)||[];if(bucket.length<2){bucket.push(s);cells.set(k,bucket);selected++}if(selected>=max)break}
    return[...cells.values()].flat().slice(0,max);
  }

  function icon(s){const v=variable(),c=cfg[v],value=num(s[v]),co=color(v,value),cls=value===null?' no-data':(!fresh(s)&&Number.isFinite(Number(s.ageMinutes))?' stale-data':'');return L.divIcon({className:'sideral-station-icon',html:`<div class="station-square${cls}" style="background:${co};color:${textColor(co)}">${esc(value===null?'—':fmt(value,c.dec))}</div>`,iconSize:[46,34],iconAnchor:[23,17],popupAnchor:[0,-18],tooltipAnchor:[0,-18]})}
  const card=(l,v,u='',d=1)=>`<div class="obs-card"><div class="obs-card-label">${esc(l)}</div><div class="obs-card-value">${fmt(v,d)}${num(v)!==null&&u?`<span class="obs-card-unit"> ${esc(u)}</span>`:''}</div></div>`;
  function when(s){if(!s.observedAt)return'horário não informado';const d=new Date(s.observedAt);if(Number.isNaN(d.getTime()))return String(s.observedAt);return`${d.toLocaleString('pt-BR',{timeZone:'America/Sao_Paulo',dateStyle:'short',timeStyle:'short'})} BRT${Number.isFinite(Number(s.ageMinutes))?` · ${s.ageMinutes} min atrás`:''}`}
  function popup(s){const e=s.extra||{},place=[s.city,s.uf].filter(Boolean).join(' / ')||'—',details=`${e.river?`<b>Rio</b><span>${esc(e.river)}</span>`:''}${e.basin?`<b>Bacia</b><span>${esc(e.basin)}</span>`:''}${e.flightCategory?`<b>Categoria de voo</b><span>${esc(e.flightCategory)}</span>`:''}`;return`<div class="popup-title">${esc(s.name||s.code)}</div><div class="popup-code">${esc(s.code||'SEM CÓDIGO')} · ${esc(source(s.network))}</div><div class="popup-grid"><b>Local</b><span>${esc(place)}</span><b>Tipo</b><span>${esc(s.kind||'estação')}</span><b>Latitude</b><span>${fmt(s.lat,5)}</span><b>Longitude</b><span>${fmt(s.lon,5)}</span><b>Altitude</b><span>${num(s.altitude)===null?'—':fmt(s.altitude,0)+' m'}</span>${details}</div><div class="obs-divider"></div><div class="obs-header"><div class="obs-title">Observação real</div><span class="source-badge">Fonte: ${esc(source(s.network))}</span></div><div class="obs-time ${fresh(s)?'obs-fresh':'obs-stale'}">${esc(when(s))}</div><div class="obs-cards">${card('Temperatura',s.temperature,'°C',1)}${card('Umidade',s.humidity,'%',0)}${card('Pressão',s.pressure,'hPa',1)}${card('Ponto de orvalho',s.dewpoint,'°C',1)}${card('Vento',s.windSpeed,'km/h',0)}${card('Rajada',s.windGust,'km/h',0)}${card('Chuva 1 h',s.rain1h,'mm',1)}${card('Chuva 24 h',s.rain24h,'mm',1)}${card('Nível do rio',s.riverLevel,'m',2)}${card('Radiação',s.radiation,'kJ/m²',0)}</div><div class="popup-footer">Estações Sideral agrega redes observacionais e mantém o crédito da fonte original. Valores ausentes não são preenchidos por modelo numérico.</div></div>`}

  function draw(){layer.clearLayers();visible=pick(filtered);for(const s of visible){L.marker([s.lat,s.lon],{icon:icon(s),keyboard:true}).bindTooltip(`<b>${esc(s.code||'')}</b> · ${esc(s.name||'')}<br>${esc(source(s.network))}`,{direction:'top',className:'station-label',offset:[0,-4]}).bindPopup(()=>popup(s),{maxWidth:600,minWidth:340}).addTo(layer)}status()}
  function status(){const recent=filtered.filter(fresh).length,errors=Object.entries(meta?.sources||{}).filter(([,m])=>m?.error),warn=errors.length?`<br><span class="source-warning">Fonte temporariamente limitada: ${errors.map(([n])=>esc(source(n))).join(', ')}</span>`:'';document.getElementById('stationStatus').innerHTML=`<b>${visible.length.toLocaleString('pt-BR')}</b> no mapa · <b>${filtered.length.toLocaleString('pt-BR')}</b> filtradas · <b>${(meta?.count||all.length).toLocaleString('pt-BR')}</b> na base<br>${recent.toLocaleString('pt-BR')} com atualização até 2 h${warn}`}

  async function load(force=false){const id=++serial,btn=document.getElementById('reloadStations');btn.disabled=true;document.getElementById('stationStatus').textContent=force?'Atualizando redes observacionais...':'Carregando redes observacionais...';const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),35000);try{const r=await fetch(force?`${ENDPOINT}?refresh=1`:ENDPOINT,{headers:{Accept:'application/json'},signal:controller.signal,cache:'no-store'}),data=await r.json();if(!r.ok||!data?.status||!Array.isArray(data.stations))throw new Error(data?.error||`HTTP ${r.status}`);if(id!==serial)return;meta=data;all=data.stations.filter(s=>num(s.lat)!==null&&num(s.lon)!==null);filters();apply()}catch(e){if(id!==serial)return;document.getElementById('stationStatus').innerHTML=`<b>Não foi possível atualizar as estações.</b><br>${esc(e?.name==='AbortError'?'tempo limite excedido':e?.message||String(e))}${all.length?'<br>Os últimos dados válidos continuam no mapa.':''}`}finally{clearTimeout(timer);if(id===serial)btn.disabled=false}}

  ['networkFilter','ufFilter','freshnessFilter'].forEach(id=>document.getElementById(id).addEventListener('change',apply));
  document.getElementById('variableFilter').addEventListener('change',()=>{legend();draw()});
  document.getElementById('stationSearch').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(apply,180)});
  document.getElementById('reloadStations').addEventListener('click',()=>load(true));
  map.on('moveend zoomend',()=>{clearTimeout(moveTimer);moveTimer=setTimeout(draw,120)});
  window.addEventListener('resize',()=>{if(innerWidth>800)openPanel(false);map.invalidateSize(false)});
  legend();load();setInterval(()=>load(false),10*60*1000);
})();
