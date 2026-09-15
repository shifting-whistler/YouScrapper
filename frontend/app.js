const FIELDS = {
  published:{label:'Published date & time'}, title:{label:'Title'}, description:{label:'Description'},
  url:{label:'Video URL'}, id:{label:'Video ID'}, channel:{label:'Channel name'},
  channel_url:{label:'Channel URL'}, thumbnail:{label:'Thumbnail URL'}, duration:{label:'Duration'},
  views:{label:'Views'}, likes:{label:'Likes'}
};

const state = {
  mode:'channel', records:[], selectedFields:Object.keys(FIELDS), visibleFields:Object.keys(FIELDS),
  sort:{key:'published_ts',dir:'desc'}, search:'', running:false,
  etaStart:null, sources:[], playlists:[], selectedPlaylistIds:new Set(), currentPlaylistId:'__all__', lastProcessed:0
};

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const els = {
  headerStatus:$('#headerStatus'), resultsPanel:$('#resultsPanel'), exportPanel:$('#exportPanel'), playlistSelect:$('#playlistSelect'),
  resultContext:$('#resultContext'), stats:$('#stats'), table:$('#resultsTable'), search:$('#search'),
  pageInfo:$('#pageInfo'), columnMenu:$('#columnMenu'), columnChecklist:$('#columnChecklist'),
  exportSummary:$('#exportSummary'), errorChannel:$('#channelError'), errorPlaylist:$('#playlistError')
};

function toast(text){const t=$('#toast');t.textContent=text;t.hidden=false;clearTimeout(toast._timer);toast._timer=setTimeout(()=>t.hidden=true,2600)}
function showTab(id){$$('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===id));$$('.tab-page').forEach(p=>p.classList.toggle('active',p.id===id));const about=id==='aboutTab';els.resultsPanel.hidden=about;els.exportPanel.hidden=about;}
$$('.tab').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.tab)));

function buildFieldGrid(target){target.innerHTML=Object.entries(FIELDS).map(([k,v])=>`<label class="field-item"><input type="checkbox" value="${k}" checked><span>${v.label}</span></label>`).join('')}
buildFieldGrid($('#channelFieldGrid')); buildFieldGrid($('#playlistFieldGrid'));
function setGridFields(id,checked){$(`#${id}`).querySelectorAll('input').forEach(x=>x.checked=checked)}
$('#selectAllChannel').onclick=()=>setGridFields('channelFieldGrid',true);
$('#clearAllChannel').onclick=()=>setGridFields('channelFieldGrid',false);
$('#selectAllPlaylistFields').onclick=()=>setGridFields('playlistFieldGrid',true);
$('#clearAllPlaylistFields').onclick=()=>setGridFields('playlistFieldGrid',false);
function selectedFields(id){return [...$(`#${id}`).querySelectorAll('input:checked')].map(x=>x.value)}
function escapeHtml(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function formatValue(key,v){if(v==null)return '';if(key==='views'||key==='likes')return Number.isFinite(Number(v))?Number(v).toLocaleString():String(v);return escapeHtml(v)}
function imgOrFallback(url,alt=''){
  const safe=escapeHtml(url||'');
  return `<img class="playlist-thumb" src="${safe}" alt="${escapeHtml(alt)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2254%22 height=%2234%22 viewBox=%220 0 54 34%22%3E%3Crect width=%2254%22 height=%2234%22 fill=%22%23161a20%22/%3E%3Cpath d=%22M19 11h16v12H19z%22 fill=%22%23313740%22/%3E%3Cpath d=%22M24 14l7 3-7 3z%22 fill=%22%23b84d59%22/%3E%3C/svg%3E'">`;
}
function formatEta(sec){if(!Number.isFinite(sec)||sec<=0)return '—';if(sec<60)return `${Math.ceil(sec)}s`;const m=Math.floor(sec/60),s=Math.ceil(sec%60);if(m<60)return `${m}m ${s}s`;const h=Math.floor(m/60);return `${h}h ${m%60}m`}
function applyProgress(s,prefix){
  const total=Number(s.total),processed=Number(s.processed||0); const known=Number.isFinite(total)&&total>0; const pct=known?Math.max(0,Math.min(100,processed/total*100)):0;
  $(`#${prefix}ProgressBar`).style.width=pct+'%'; $(`#${prefix}ProgressPercent`).textContent=known?`${pct.toFixed(2)}%`:'—';
  $(`#${prefix}ProgressCount`).textContent=known?`${processed.toLocaleString()} / ${total.toLocaleString()}`:`${processed.toLocaleString()} / —`;
  $(`#${prefix}ProgressMessage`).textContent=s.message||'';
  $(`#${prefix}CurrentItem`).textContent=s.current_title?`Current: ${s.current_title}${s.current_playlist?' · '+s.current_playlist:''}`:'';
  if(known&&processed>0&&state.etaStart){const speed=processed/Math.max((Date.now()-state.etaStart)/1000,.1);const remain=(total-processed)/Math.max(speed,.001);$(`#${prefix}Eta`).textContent=`ETA ${formatEta(remain)}`}
  else $(`#${prefix}Eta`).textContent='ETA —';
}
function setHeader(status){const dot=els.headerStatus.querySelector('.status-dot');const label=els.headerStatus.querySelector('span:last-child');dot.classList.toggle('running',['discovering','extracting'].includes(status));dot.classList.toggle('error',status==='error');label.textContent=status==='extracting'?'Extracting…':status==='discovering'?'Discovering…':status==='complete'?'Complete':status==='partial'?'Partial':status==='cancelled'?'Stopped':status==='error'?'Error':'Ready'}
function applyStatus(s){
  setHeader(s.status); state.mode=s.mode||state.mode; state.running=['discovering','extracting'].includes(s.status);
  const pfx=state.mode==='playlist'?'playlist':'channel'; const wrap=$(`#${pfx}ProgressWrap`); if(wrap)wrap.hidden=s.status==='idle';
  $(`#${pfx}ExtractBtn`).disabled=state.running; $(`#${pfx}CancelBtn`).hidden=!state.running; applyProgress(s,pfx);
  const box=state.mode==='playlist'?els.errorPlaylist:els.errorChannel;
  if(s.status==='error'){box.hidden=false;box.textContent=s.message||'Extraction failed.';toast('Extraction failed')}
  else if(['complete','partial','cancelled'].includes(s.status)){if(s.status==='complete')toast('Extraction complete');else if(s.status==='partial')toast('Extraction completed partially');else toast('Extraction stopped')}
}
function searchMatch(r,q){if(!q)return true;const n=q.normalize('NFC').toLocaleLowerCase();return ['title','description','url','id','channel','channel_url','playlist_name','source_title'].some(k=>String(r[k]??'').normalize('NFC').toLocaleLowerCase().includes(n))}
function compare(a,b,key){
  if(key==='playlist_order'){
    const rank=r=>{const i=state.playlists.findIndex(p=>String(p.id)===String(r.playlist_id));return i<0?Number.MAX_SAFE_INTEGER:i};
    const ar=rank(a),br=rank(b); if(ar!==br)return ar-br;
    return (Number(a.playlist_index)||0)-(Number(b.playlist_index)||0)
  }
  if(key==='published')key='published_ts'; let av=a[key],bv=b[key];
  if(['views','likes','duration_seconds'].includes(key))return (Number(av)||-Infinity)-(Number(bv)||-Infinity);
  if(key==='playlist_index')return (Number(av)||0)-(Number(bv)||0);
  return String(av??'').localeCompare(String(bv??''),undefined,{numeric:true,sensitivity:'base'})
}
function currentRows(){
  const arr=state.records.filter(r=>{
    if(state.mode==='playlist'&&state.currentPlaylistId!=='__all__'&&String(r.playlist_id)!==String(state.currentPlaylistId))return false;
    return searchMatch(r,state.search)
  });
  arr.sort((a,b)=>compare(a,b,state.sort.key)*(state.sort.dir==='asc'?1:-1));
  return arr
}
function buildColumns(){els.columnChecklist.innerHTML=Object.entries(FIELDS).map(([k,v])=>`<label><input type="checkbox" value="${k}" ${state.visibleFields.includes(k)?'checked':''}>${v.label}</label>`).join('');els.columnChecklist.querySelectorAll('input').forEach(i=>i.addEventListener('change',()=>{state.visibleFields=[...els.columnChecklist.querySelectorAll('input:checked')].map(x=>x.value);renderTable()}))}
function renderPlaylistSelect(){
  const select=els.playlistSelect;
  if(!select)return;
  select.closest('.results-filter-row').hidden=state.mode!=='playlist';
  const counts=new Map();
  state.records.forEach(r=>counts.set(String(r.playlist_id),(counts.get(String(r.playlist_id))||0)+1));
  const current=state.currentPlaylistId;
  select.innerHTML=`<option value="__all__">All playlists · ${state.records.length.toLocaleString()} records</option>`+
    state.playlists.map(p=>`<option value="${escapeHtml(p.id)}">${escapeHtml(p.title)} · ${(counts.get(String(p.id))||0).toLocaleString()} videos${p.source_title?` · ${escapeHtml(p.source_title)}`:''}</option>`).join('');
  select.value=state.playlists.some(p=>String(p.id)===String(current))?String(current):'__all__';
}
function renderStats(){
  const total=state.records.length,shown=currentRows().length;
  els.stats.innerHTML=`<span><b>${total.toLocaleString()}</b> records</span><span><b>${shown.toLocaleString()}</b> shown</span>`;
  els.exportSummary.textContent=`${shown.toLocaleString()} records ready to export · ${state.visibleFields.length} columns selected${state.mode==='playlist'?' · Playlist grouping preserved':''}`;
  els.resultContext.textContent=state.mode==='playlist'?(state.currentPlaylistId==='__all__'?'Playlist Organizer · all selected playlists':`Playlist · ${state.playlists.find(p=>String(p.id)===String(state.currentPlaylistId))?.title||'Selected playlist'}`):'Channel Archive · complete extracted dataset';
}
function sortKeyFor(k){return k==='published'?'published_ts':k==='views'?'views':k==='likes'?'likes':k==='duration'?'duration_seconds':k}
function renderTable(){
  const visible=state.visibleFields; const head=els.table.tHead,body=els.table.tBodies[0]||els.table.createTBody();
  const keys=(state.mode==='playlist'&&state.currentPlaylistId==='__all__'?['source_title','playlist_name']:[]).concat(visible);
  head.innerHTML='<tr>'+keys.map(k=>`<th data-sort="${sortKeyFor(k)}">${k==='playlist_name'?'Playlist':k==='source_title'?'Source':FIELDS[k].label}${state.sort.key===sortKeyFor(k)?`<span class="sort-indicator">${state.sort.dir==='asc'?'↑':'↓'}</span>`:''}</th>`).join('')+'</tr>';
  head.querySelectorAll('th').forEach(th=>th.onclick=()=>{const key=th.dataset.sort;if(state.sort.key===key)state.sort.dir=state.sort.dir==='asc'?'desc':'asc';else{state.sort.key=key;state.sort.dir=key==='playlist_order'?'asc':'desc'}renderTable()});
  const arr=currentRows();
  body.innerHTML=arr.map(r=>'<tr>'+keys.map(k=>{const raw=r[k]??'';if(k==='playlist_name'||k==='source_title')return `<td><span class="${k==='playlist_name'?'playlist-chip':'source-chip'}">${escapeHtml(raw)}</span></td>`;if(k==='description'||k==='title'){const shown=String(raw),preview=shown.length>280?shown.slice(0,280)+'…':shown;return `<td><button class="cell-btn cell-preview" data-key="${k}" data-index="${r._index}">${escapeHtml(preview||'')}</button></td>`}return `<td>${formatValue(k,raw)}</td>`}).join('')+'</tr>').join('');
  body.querySelectorAll('.cell-btn').forEach(btn=>btn.onclick=()=>openModal(btn.dataset.key,state.records.find(x=>String(x._index)===String(btn.dataset.index))));
  els.pageInfo.textContent=arr.length?`${arr.length.toLocaleString()} records shown`:'No records';
  renderPlaylistSelect();renderStats();
}
function openModal(key,r){if(!r)return;$('#modalKicker').textContent=FIELDS[key].label;$('#modalTitle').textContent=r.title||'';$('#modalBody').textContent=r[key]||'';$('#detailModal').hidden=false}
function sourceId(){return `source-${Date.now()}-${Math.random().toString(36).slice(2,7)}`}
function invalidatePlaylistDiscovery(){state.sources=[];state.playlists=[];state.selectedPlaylistIds.clear();renderPlaylists()}
function addSourceRow(value=''){
  const id=sourceId();const row=document.createElement('div');row.className='source-row';row.dataset.sourceId=id;
  row.innerHTML=`<span class="source-index">${$('#sourceList').children.length+1}</span><input class="source-url-input" type="url" value="${escapeHtml(value)}" placeholder="https://www.youtube.com/@channel or https://www.youtube.com/playlist?list=..."><button class="secondary action-btn icon-btn" title="Remove source" aria-label="Remove source"><span class="btn-icon">×</span></button>`;
  row.querySelector('button').onclick=()=>{invalidatePlaylistDiscovery();if($('#sourceList').children.length===1){row.querySelector('input').value='';row.querySelector('input').focus();return}row.remove();renumberSources()};
  $('#sourceList').appendChild(row);renumberSources();return row
}
function renumberSources(){$$('#sourceList .source-row').forEach((r,i)=>r.querySelector('.source-index').textContent=String(i+1))}
function getSourceInputs(){return $$('#sourceList .source-url-input').map((input,i)=>({source_id:input.closest('.source-row').dataset.sourceId||`source-${i+1}`,url:input.value.trim()})).filter(x=>x.url)}
function sourceLabel(p){return p.source_title||p.channel_name||p.source_url||'Source'}
function renderPlaylists(){
  const q=$('#playlistSearch').value.trim().normalize('NFC').toLocaleLowerCase();
  const items=state.playlists.filter(p=>!q||[p.title,p.source_title,p.source_url].some(v=>String(v||'').normalize('NFC').toLocaleLowerCase().includes(q)));
  if(!state.playlists.length)$('#playlistList').innerHTML='<div class="empty-state">No playlists loaded yet.</div>';
  else if(!items.length)$('#playlistList').innerHTML='<div class="empty-state">No playlists match this search.</div>';
  else{
    const groups=new Map();for(const p of items){const key=String(p.source_id||p.source_url||'');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(p)}
    let html='';
    for(const [,group] of groups){html+=`<div class="playlist-group"><div class="playlist-group-head"><span>${escapeHtml(sourceLabel(group[0]))}</span><span>${group.length.toLocaleString()} playlist${group.length===1?'':'s'}</span></div>`;
      html+=group.map(p=>`<label class="playlist-item"><input type="checkbox" value="${escapeHtml(p.id)}" ${state.selectedPlaylistIds.has(String(p.id))?'checked':''}><span class="thumb-wrap">${imgOrFallback(p.thumbnail,p.title)}</span><span class="playlist-meta"><span class="playlist-title">${escapeHtml(p.title)}</span><span class="playlist-sub">${p.count==null?'Public playlist':`${Number(p.count).toLocaleString()} entries`} · ${escapeHtml(sourceLabel(p))}</span></span><span class="playlist-check">${state.selectedPlaylistIds.has(String(p.id))?'✓':''}</span></label>`).join('');
      html+='</div>';
    }
    $('#playlistList').innerHTML=html;
    $('#playlistList').querySelectorAll('input').forEach(i=>i.onchange=()=>{if(i.checked)state.selectedPlaylistIds.add(String(i.value));else state.selectedPlaylistIds.delete(String(i.value));renderPlaylists()})
  }
  updatePlaylistSelectionCount()
}
function updatePlaylistSelectionCount(){$('#playlistSelectionCount').textContent=`${state.selectedPlaylistIds.size.toLocaleString()} selected`}
async function discoverSources(){
  const box=els.errorPlaylist;box.hidden=true;const sources=getSourceInputs();
  if(!sources.length){box.hidden=false;box.textContent='Add at least one YouTube channel or playlist source.';return}
  $('#discoverSourcesBtn').disabled=true;toast('Discovering playlists from all sources…');
  try{const r=await fetch('/api/discover-sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sources})});const d=await r.json();if(!r.ok)throw new Error(d.error||'Could not discover playlists.');
    state.sources=d.sources||[];state.playlists=d.playlists||[];state.selectedPlaylistIds=new Set();state.mode='playlist';renderPlaylists();const warningCount=(d.warnings||[]).length;toast(`${state.playlists.length.toLocaleString()} playlist${state.playlists.length===1?'':'s'} discovered${warningCount?` · ${warningCount} source warning${warningCount===1?'':'s'}`:''}`);if(warningCount){box.hidden=false;box.textContent=(d.warnings||[]).join(' | ')}
  }catch(e){box.hidden=false;box.textContent=e.message}finally{$('#discoverSourcesBtn').disabled=false}
}
async function start(mode){
  const box=mode==='playlist'?els.errorPlaylist:els.errorChannel;box.hidden=true;const source=(mode==='playlist'?(state.sources[0]?.url||'') : $('#channelUrl').value.trim());const fields=selectedFields(mode==='playlist'?'playlistFieldGrid':'channelFieldGrid');
  if(!source){box.hidden=false;box.textContent=mode==='playlist'?'Add at least one source and discover playlists first.':'Enter a YouTube channel URL.';return}if(!fields.length){box.hidden=false;box.textContent='Select at least one metadata field.';return}
  let playlistIds=[];if(mode==='playlist'){playlistIds=[...state.selectedPlaylistIds];if(!playlistIds.length){box.hidden=false;box.textContent='Discover playlists and select at least one playlist.';return}}
  state.mode=mode;state.records=[];state.selectedFields=fields;state.visibleFields=[...fields];state.currentPlaylistId='__all__';state.sort=mode==='playlist'?{key:'playlist_order',dir:'asc'}:{key:'published_ts',dir:'desc'};state.search='';$('#search').value='';buildColumns();renderTable();state.etaStart=null;state.lastProcessed=0;
  try{const r=await fetch('/api/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode,source_url:source,fields,playlist_ids:playlistIds,playlists:mode==='playlist'?state.playlists.filter(p=>state.selectedPlaylistIds.has(String(p.id))):[],sources:mode==='playlist'?state.sources:[],playlist_title:state.playlists[0]?.title||''})});const d=await r.json();if(!r.ok)throw new Error(d.error||'Could not start extraction.');applyStatus({status:'discovering',message:'Starting extraction…',mode});}
  catch(e){box.hidden=false;box.textContent=e.message;setHeader('error')}
}
async function cancel(){try{await fetch('/api/cancel',{method:'POST'});toast('Stopping extraction…')}catch{toast('Could not contact the extractor')}}
async function exportData(kind){
  const rows=currentRows();if(!rows.length){toast('No records match the current view');return}if(!state.visibleFields.length){toast('Select at least one export column');return}
  const ids=rows.map(r=>r._index);toast(kind==='pdf'?'Generating PDF…':'Preparing export…');
  try{const r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,columns:state.visibleFields,ids})});if(!r.ok){const d=await r.json();throw new Error(d.error||'Export failed')}const blob=await r.blob();const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`YouScraper.${kind}`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast(`${kind.toUpperCase()} export ready`)}catch(e){toast(e.message)}
}

$('#clearChannel').onclick=()=>{$('#channelUrl').value='';$('#channelUrl').focus()};
$('#discoverSourcesBtn').onclick=discoverSources;
$('#addSourceBtn').onclick=()=>{invalidatePlaylistDiscovery();addSourceRow('')};
$('#selectAllPlaylists').onclick=()=>{state.playlists.forEach(p=>state.selectedPlaylistIds.add(String(p.id)));renderPlaylists()};
$('#clearAllPlaylists').onclick=()=>{state.selectedPlaylistIds.clear();renderPlaylists()};
$('#playlistSearch').oninput=renderPlaylists;
$('#channelExtractBtn').onclick=()=>start('channel');$('#playlistExtractBtn').onclick=()=>start('playlist');$('#channelCancelBtn').onclick=cancel;$('#playlistCancelBtn').onclick=cancel;
els.search.oninput=()=>{state.search=els.search.value.trim();renderTable()};
els.playlistSelect?.addEventListener('change',()=>{state.currentPlaylistId=els.playlistSelect.value;renderTable()});
$('#columnBtn').onclick=()=>els.columnMenu.hidden=!els.columnMenu.hidden;$('#closeColumns').onclick=()=>els.columnMenu.hidden=true;
$('#resetView').onclick=()=>{state.search='';els.search.value='';state.sort=state.mode==='playlist'?{key:'playlist_order',dir:'asc'}:{key:'published_ts',dir:'desc'};state.currentPlaylistId='__all__';renderTable()};
$('#modalClose').onclick=()=>$('#detailModal').hidden=true;$('#detailModal').onclick=e=>{if(e.target===$('#detailModal'))$('#detailModal').hidden=true};
$('#copyEmail').onclick=async()=>{try{await navigator.clipboard.writeText('shiftingwhistler@gmail.com')}catch{const ta=document.createElement('textarea');ta.value='shiftingwhistler@gmail.com';document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove()}toast('Copied!')};
$$('[data-external]').forEach(el=>el.addEventListener('click',()=>window.youtubeExternal?.open(el.dataset.external)));
$$('[data-export]').forEach(el=>el.addEventListener('click',()=>exportData(el.dataset.export)));
document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('#detailModal').hidden=true;els.columnMenu.hidden=true}});

let eventSource=null;
function mergeRecords(incoming){for(const r of incoming){const ix=String(r._index);const pos=state.records.findIndex(x=>String(x._index)===ix);if(pos>=0)state.records[pos]=r;else state.records.push(r)}}
function handleEvent(msg){
  if(msg.type==='status'){if(msg.status==='extracting'&&state.etaStart===null)state.etaStart=Date.now();if(msg.status==='extracting'&&Number(msg.processed||0)<state.lastProcessed)state.etaStart=Date.now();state.lastProcessed=Number(msg.processed||0);applyStatus(msg)}
  else if(msg.type==='archive_discovered'){state.mode=msg.mode||state.mode;state.records=[];state.etaStart=Date.now();state.lastProcessed=0;renderTable();toast(`${Number(msg.total||0).toLocaleString()} video entries discovered`)}
  else if(msg.type==='records'){mergeRecords(msg.records||[]);renderTable()}
  else if(msg.type==='done'){refreshResults()}
}
function connectEvents(){
  if(eventSource)eventSource.close();eventSource=new EventSource('/api/events');eventSource.onmessage=e=>{try{handleEvent(JSON.parse(e.data))}catch(err){console.error('SSE message error',err)}};eventSource.onerror=()=>{if(eventSource){setTimeout(()=>{if(eventSource&&eventSource.readyState===EventSource.CLOSED)connectEvents()},1200)}};return eventSource
}
async function fetchStatus(){try{const r=await fetch('/api/status',{cache:'no-store'});const s=await r.json();applyStatus(s)}catch{}}
async function refreshResults(){try{const r=await fetch('/api/results',{cache:'no-store'});const d=await r.json();state.records=d.records||[];state.selectedFields=d.selected_fields||state.selectedFields;state.visibleFields=[...state.selectedFields];state.mode=d.mode||state.mode;state.sources=d.sources||state.sources;state.playlists=d.playlists||state.playlists;state.selectedPlaylistIds=new Set();state.currentPlaylistId='__all__';buildColumns();renderPlaylists();renderTable();await fetchStatus()}catch{}}

buildColumns();renderTable();if(!$('#sourceList').children.length)addSourceRow('');renderPlaylists();connectEvents();fetchStatus();refreshResults();
setInterval(fetchStatus,750);
