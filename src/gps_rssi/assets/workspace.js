'use strict';
const $=id=>document.getElementById(id);
const activeStates=new Set(['running','stopping','unknown']);
const labels={field:'Field sweeps',results:'Results',radios:'Radios',live:'Live view',surveys:'Surveys & exports',audio:'Audio tests',offline:'Offline data'};
let state=null,token=null,editing=null,intent=null,serial=0,applied=0,pollTimer=null,history=false,closing=false;
const radioCards=new Map(),jobCards=new Map(),processedResults=new Set(),previousJobStates=new Map();

function node(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(className)n.className=className;return n;}
function button(label,action,className='secondary'){const b=node('button',label,className);b.type='button';b.onclick=()=>act(b,action);return b;}
function notify(message,error=false,activity=false){$('notice-text').textContent=message;$('notice').classList.toggle('error',error);$('notice').hidden=false;$('notice').setAttribute('role',error?'alert':'status');$('notice-activity').hidden=!activity;}
async function api(path,data){
  if(!token)throw new Error('The workspace is still connecting. Try again in a moment.');
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-GPS-RSSI-Workspace':token},body:JSON.stringify(data)});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'The action could not be completed.');return result;
}
async function act(control,action){
  if(control.dataset.busy==='true')return;
  control.dataset.busy='true';control.disabled=true;
  try{await action();}catch(error){notify(error.message,true);}finally{control.dataset.busy='false';control.disabled=false;if(state)render();}
}
function setButton(control,label,disabled=false){control.textContent=label;control.disabled=disabled||control.dataset.busy==='true'||closing;}
function view(){const current=Object.hasOwn(labels,location.hash.slice(1))?location.hash.slice(1):'radios';
  for(const name of Object.keys(labels))$('view-'+name).hidden=name!==current;
  for(const link of document.querySelectorAll('[data-view]')){if(link.dataset.view===current)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
  $('current-view').textContent=labels[current];document.body.classList.toggle('field-night',current==='field'&&$('view-field').classList.contains('night'));if(current!=='field'){document.body.classList.remove('field-focus');if($('field-focus')){$('field-focus').setAttribute('aria-pressed','false');$('field-focus').textContent='Focus view';}}
}
window.addEventListener('hashchange',view);view();
function age(value){const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(value))/1000));if(!Number.isFinite(seconds))return 'unknown';return seconds<60?seconds+'s':seconds<3600?Math.floor(seconds/60)+' min':Math.floor(seconds/3600)+' hr';}
function timestamp(value){return new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'});}
function activeFor(profile){return state.jobs.find(j=>j.profile===profile&&activeStates.has(j.state));}
function radioIdentity(address){const compact=address.replace(/[:-]/g,'').toLowerCase();return /^[0-9a-f]{12}$/.test(compact)?compact:address.toLowerCase();}
function savedRadio(candidate){return state.profiles.find(p=>!p.error&&radioIdentity(p.address)===radioIdentity(candidate.address));}
function chooseOptions(select,items,placeholder){
  const signature=JSON.stringify(items);if(select.dataset.signature===signature)return;
  const previous=select.value;select.replaceChildren();const empty=node('option',placeholder);empty.value='';select.append(empty);
  for(const item of items){const option=node('option',item.name);option.value=item.id;select.append(option);}
  if(items.some(item=>item.id===previous))select.value=previous;
  select.dataset.signature=signature;
}
async function launch(data,message){const result=await api('/api/jobs',data);notify(message||'Started. Follow progress in Activity.',false,true);await refresh();const current=state.jobs.find(j=>j.id===result.id);if(current&&['failed','unknown'].includes(current.state))notify(current.message,true,true);return result;}

function lastRadioJob(profile){return state.jobs.find(job=>job.profile===profile.id&&GpsRssiStatus.radioKinds.has(job.kind));}
function radioPresentation(profile,job=activeFor(profile.id),lastJob=lastRadioJob(profile)){return GpsRssiStatus.radioStatus(profile,job,{lastJob,now:Date.now(),workspaceAt:lastStateReceivedAt,bluetooth:state.capabilities.bluetooth});}
function showOperation(id){history=true;renderJobs();const item=jobCards.get(id);if(item){item.details.open=true;item.card.tabIndex=-1;item.card.scrollIntoView({block:'center'});item.card.focus({preventScroll:true});}}
function showRadio(id){location.hash='radios';renderRadios();const item=radioCards.get(id);if(item){item.card.tabIndex=-1;item.card.scrollIntoView({block:'center'});item.card.focus({preventScroll:true});}}
function openFieldFor(profile,job){selectSession(job?.survey_session||'');$('field-radio').value=profile.id;$('field-radio').onchange();location.hash='field';}
function recoverRadio(id){const profile=state.profiles.find(p=>p.id===id);if(!profile)return;const job=activeFor(id)||lastRadioJob(profile),status=radioPresentation(profile);if(status.action==='field')openFieldFor(profile,job);else if(status.action==='audio'){if(profile)$('transmitter-radio').value=profile.id;location.hash='audio';renderAudioButtons();}else if(status.action==='setup'){if(profile.error&&state.capabilities.setup)openEditor(null);else location.hash='audio';}else if(status.action==='activity'&&job)showOperation(job.id);else if(status.action==='live'){chooseLive(id);location.hash='live';}}

function createRadioCard(profile){
  const card=node('article',undefined,'radio-card');card.dataset.profile=profile.id;
  const top=node('div',undefined,'radio-top'),heading=node('div'),name=node('h3',undefined,'radio-name'),role=node('p',undefined,'radio-role'),badge=node('span',undefined,'badge');
  heading.append(name,role);top.append(heading,badge);
  const notes=node('p',undefined,'radio-notes'),stats=node('div',undefined,'radio-stats'),signal=node('div'),signalValue=node('strong'),signalLabel=node('small','Receive RSSI /100'),meter=node('div',undefined,'signal-meter'),fill=node('span');meter.append(fill);signal.append(signalValue,signalLabel,meter);
  const gps=node('div'),gpsValue=node('strong'),gpsLabel=node('small','GPS lock');gps.append(gpsValue,gpsLabel);stats.append(signal,gps);
  const freshness=node('p',undefined,'radio-freshness'),detail=node('p',undefined,'radio-status-detail'),guidance=node('p',undefined,'radio-guidance'),recovery=button('View operation',()=>recoverRadio(profile.id),'quiet radio-recovery'),actions=node('div',undefined,'radio-actions');
  const start=button('Start recording',async()=>{const job=activeFor(profile.id);if(job&&job.kind==='record'){await api('/api/stop',{id:job.id});await refresh();}else await launch({kind:'record',profile:profile.id},'Recorder started. Check for recent telemetry on its radio card.');});
  start.className='primary';
  const live=button('Live view',()=>{chooseLive(profile.id);location.hash='live';},'secondary');
  const share=button('Snapshot',()=>launch({kind:'share',database:'profile:'+profile.id},'Creating a portable recording. Download it from Activity.'),'quiet');
  const edit=button('Edit',()=>openEditor(state.profiles.find(p=>p.id===profile.id)),'quiet edit-radio');
  guidance.id='radio-guidance-'+profile.id;start.setAttribute('aria-describedby',guidance.id);actions.append(start,live,share);card.append(top,notes,stats,freshness,detail,guidance,recovery,actions,edit);
  $('radio-list').append(card);
  return {card,name,role,badge,notes,stats,signalValue,signalLabel,gpsValue,gpsLabel,fill,freshness,detail,guidance,recovery,start,live,share,edit};
}
function renderRadios(){
  $('configured-count').textContent=String(state.profiles.filter(p=>!p.error).length);
  $('recording-count').textContent=String(state.jobs.filter(j=>(j.kind==='record'||j.result?.recording)&&j.state==='running').length);
  $('saved-count').textContent=String(state.datasets.databases.length);
  $('radios-empty').hidden=state.profiles.length>0;
  for(const profile of state.profiles){
    if(!radioCards.has(profile.id))radioCards.set(profile.id,createRadioCard(profile));
    const c=radioCards.get(profile.id),job=activeFor(profile.id),status=radioPresentation(profile,job),latest=status.displaySample;
    c.name.textContent=profile.name;c.role.textContent=profile.error?'Setup needs attention':`${profile.role==='fixed'?'Fixed station':'Mobile station'} · ${profile.gps?'GPS enabled':'GPS off'}`;
    c.notes.textContent=profile.error?'Saved setup: '+profile.id:profile.label||'Add model and antenna notes in this radio’s setup.';
    c.badge.className='badge '+status.tone;c.badge.textContent=status.label;c.card.dataset.connection=status.code;
    const hasSignal=latest&&latest.is_in_rx&&!latest.is_in_tx&&latest.rssi_unit==='benshi_relative_0_100'&&Number.isFinite(latest.rssi)&&latest.rssi>=0&&latest.rssi<=100;
    c.signalValue.textContent=hasSignal?latest.rssi.toFixed(1):'—';c.fill.style.width=hasSignal?latest.rssi+'%':'0%';
    c.signalLabel.textContent=status.currentSample?'Receive RSSI /100':'Last recorded RSSI /100';c.stats.classList.toggle('historical',!status.currentSample);
    c.gpsValue.textContent=!profile.gps?'Disabled':!latest?'Unknown':latest.gps_locked?'Locked':'No lock';
    c.gpsLabel.textContent=!profile.gps?'GPS recording':!latest?'No GPS telemetry yet':status.currentSample?'GPS lock':'Last recorded GPS lock';
    c.freshness.textContent=profile.data_error||(!latest?'No recorded telemetry yet':`${status.currentSample?'Latest':'Last saved'} telemetry ${age(latest.received_at)} ago · ${latest.is_in_tx?'TX':latest.is_in_rx?'RX':'Idle'}`);
    c.detail.textContent=status.detail;c.guidance.textContent=status.guidance;
    c.recovery.hidden=!status.action||status.action==='live';
    setButton(c.recovery,status.action==='field'?'Open Field sweeps':status.action==='audio'?'Open Audio tests':status.action==='setup'?(profile.error&&state.capabilities.setup?'Add replacement setup':'Installation details'):status.code==='unknown'?'Review stop status':'View operation');
    const canStop=job?.kind==='record'&&job.state==='running';
    setButton(c.start,job?.kind==='record'?(job.state==='stopping'?'Stopping…':'Stop recording'):'Start recording',!canStop&&(!!profile.error||!state.capabilities.bluetooth||!!job||!GpsRssiStatus.workspaceFresh(Date.now(),lastStateReceivedAt)));
    setButton(c.live,'Live view',!job||job.kind!=='record'||job.state!=='running');
    setButton(c.share,'Snapshot',!profile.has_data||!GpsRssiStatus.workspaceFresh(Date.now(),lastStateReceivedAt));setButton(c.edit,'Edit',!!job||!!profile.error||!state.capabilities.setup);
  }
  for(const [id,c] of radioCards)if(!state.profiles.some(p=>p.id===id)){c.card.remove();radioCards.delete(id);}
  setButton($('scan-radios'),state.jobs.some(j=>j.kind==='scan'&&activeStates.has(j.state))?'Searching…':'Find nearby radios',!state.capabilities.bluetooth||state.jobs.some(j=>j.kind==='scan'&&activeStates.has(j.state)));
  setButton($('add-radio'),'＋ Add radio',!state.capabilities.setup);setButton($('empty-add-radio'),'Add your first radio',!state.capabilities.setup);
}

let liveProfile=null,liveJob=null;
function chooseLive(id){liveProfile=id;renderLive();}
function renderLive(force=false){
  const running=state.jobs.filter(j=>j.kind==='record'&&j.state==='running');
  const items=running.map(j=>state.profiles.find(p=>p.id===j.profile)).filter(Boolean);
  chooseOptions($('live-radio'),items,'Choose a running recorder');
  if(liveProfile&&items.some(p=>p.id===liveProfile))$('live-radio').value=liveProfile;
  if(!liveProfile&&items.length===1){liveProfile=items[0].id;$('live-radio').value=liveProfile;}
  const selected=items.find(p=>p.id===$('live-radio').value),job=selected&&running.find(j=>j.profile===selected.id);
  $('live-status').hidden=!selected;if(selected){const status=radioPresentation(selected,job);$('live-status-label').textContent=status.label;$('live-status-detail').textContent=status.detail+' '+status.guidance;$('live-status').classList.toggle('warning',status.tone!=='good');}
  $('live-empty').hidden=!!selected;$('live-frame').hidden=!selected;$('live-external').hidden=!selected;$('reload-live').disabled=!selected;
  if(selected){const url='http://127.0.0.1:'+selected.port+'/';$('live-external').href=url;
    if(liveJob!==job.id||force){$('live-frame').src=url+(force?'?refresh='+Date.now():'');liveJob=job.id;}
  }else if(liveJob){$('live-frame').src='about:blank';liveJob=null;}
}
$('live-radio').onchange=()=>{liveProfile=$('live-radio').value;renderLive();};$('reload-live').onclick=()=>renderLive(true);

let databaseSignature='';
function renderDatasets(){
  const databases=state.datasets.databases,signature=JSON.stringify(databases);
  if(databaseSignature!==signature){
    const previous=new Set([...$('database-list').querySelectorAll('input:checked')].map(n=>n.value)),first=databaseSignature==='';
    $('database-list').replaceChildren();
    for(const dataset of databases){const label=node('label',undefined,'dataset-row'),checkbox=node('input');checkbox.type='checkbox';checkbox.value=dataset.id;checkbox.checked=first||previous.has(dataset.id);checkbox.onchange=renderDatasetButtons;
      const details=node('span');details.append(node('strong',dataset.name),node('small',dataset.source==='radio'?'Saved radio recording':dataset.source==='import'?'Imported recording':'Combined or portable recording'));label.append(checkbox,details);$('database-list').append(label);}
    if(!databases.length)$('database-list').append(node('p','No recordings yet. Start a radio or import a portable .sqlite file.','dataset-empty'));
    databaseSignature=signature;
  }
  chooseOptions($('export-database'),databases,'Choose a recording');if(!$('export-database').value&&databases.length===1)$('export-database').value=databases[0].id;
  chooseOptions($('analysis-audio'),state.datasets.audio,'Choose a completed capture or imported WAV');if(!$('analysis-audio').value&&state.datasets.audio.length===1)$('analysis-audio').value=state.datasets.audio[0].id;
  const profiles=state.profiles.filter(p=>!p.error);for(const id of ['receiver-radio','transmitter-radio'])chooseOptions($(id),profiles,'Choose a saved radio');

  renderDatasetButtons();
}
function selectedDatabases(){return [...$('database-list').querySelectorAll('input:checked')].map(n=>n.value);}
function renderDatasetButtons(){
  const selected=selectedDatabases().length>0;setButton($('build-survey'),'Create survey',!selected);setButton($('merge-recordings'),'Merge recordings only',!selected);
  const available=!!$('export-database').value;setButton($('export-file'),'Export file',!available);setButton($('share-recording'),'Create portable snapshot',!available);
}
$('export-database').onchange=renderDatasetButtons;
$('create-demo').onclick=()=>act($('create-demo'),()=>launch({kind:'demo'},'Creating synthetic test data. Open the test survey from Activity to try the offline presets.'));
$('build-survey').onclick=()=>act($('build-survey'),()=>launch({kind:'survey',databases:selectedDatabases(),source:$('survey-source').value},'Building your shared survey. The map and GIS files will appear in Activity.'));
$('merge-recordings').onclick=()=>act($('merge-recordings'),()=>launch({kind:'merge',databases:selectedDatabases()},'Combining recordings without duplicate samples.'));
$('export-file').onclick=()=>act($('export-file'),()=>launch({kind:'export',database:$('export-database').value,format:$('export-format').value,source:$('survey-source').value},'Creating your export. Download it from Activity.'));
$('share-recording').onclick=()=>act($('share-recording'),()=>launch({kind:'share',database:$('export-database').value},'Creating a standalone snapshot, including committed recording data.'));

function fileURL(job,name,download=false){return '/files/'+encodeURIComponent(job.id)+'/'+encodeURIComponent(name)+(download?'?download=1':name.endsWith('.html')?'?tile_token='+encodeURIComponent(state.tile_token):'');}
function link(label,url,className='button secondary'){const a=node('a',label,className);a.href=url;a.target='_blank';a.rel='noopener';return a;}
function createJob(job){
  const card=node('article',undefined,'job'),heading=node('div',undefined,'job-header'),title=node('h3'),badge=node('span',undefined,'badge');heading.append(title,badge);
  const message=node('p'),guidance=node('p',undefined,'job-guidance'),when=node('time'),actions=node('div',undefined,'actions'),media=node('div'),downloads=node('details'),downloadTitle=node('summary','Download files'),fileLinks=node('div',undefined,'file-links');downloads.append(downloadTitle,fileLinks);
  const details=node('details'),summary=node('summary','Operation details'),log=node('pre');details.append(summary,log);
  card.dataset.job=job.id;card.append(heading,message,guidance,when,actions,media,downloads,details);$('activity-list').prepend(card);
  return {card,title,badge,message,guidance,when,actions,media,downloads,fileLinks,details,log,actionSignature:''};
}
function renderJobs(){
  const active=state.jobs.filter(j=>activeStates.has(j.state));$('active-count').textContent=active.length+' active';$('activity-empty').hidden=state.jobs.length>0;
  const shown=state.jobs.filter((j,i)=>history||i<5||activeStates.has(j.state));
  for(const job of [...shown].reverse()){
    if(!jobCards.has(job.id))jobCards.set(job.id,createJob(job));const c=jobCards.get(job.id);
    c.title.textContent=job.title;c.badge.className='badge '+job.state;c.badge.textContent={running:'In progress',stopping:'Stopping',completed:'Ready',stopped:'Stopped',failed:'Failed',unknown:'Check radio',interrupted:'Reviewed'}[job.state]||job.state;
    const profile=state.profiles.find(p=>p.id===job.profile),presentation=profile&&GpsRssiStatus.radioKinds.has(job.kind)&&activeStates.has(job.state)?radioPresentation(profile,job):null;
    let message=job.message;
    if(job.state==='running')message=job.kind==='record'?'Recording is running. Check the radio card for recent telemetry.':job.kind==='transmit'?(job.result?.state==='transmitting'?'Sending the test tone.':'Connecting to the transmitter…'):job.kind==='capture'?(job.result?.state==='capturing'?'Capturing received audio…':'Connecting to the receiver…'):job.kind==='scan'?'Searching for nearby Bluetooth radios…':job.kind==='survey'?'Creating the map, charts, and GIS files…':'Working…';
    if(job.state==='running'&&job.result?.automatic)message=(job.result.state==='transmitting'?'Sending 1 kHz tone.':job.result.state==='receiving'?'Listening between tones.':'Waiting for the first synchronized tone.')+' Clock: '+(job.result.clock_source||'checking GPS')+(job.result.clock_fallback?' ('+job.result.clock_fallback+')':'')+(job.result.next_tone_at?' · Next tone: '+timestamp(job.result.next_tone_at):'')+' · '+(job.result.tones_sent||0)+' tones completed.';
    if(job.kind==='preflight')message=job.state==='running'?(job.title.startsWith('Passive preflight · ')?'Checking read-only radio control and recording destination. No audio connection or test tones.':'Checking radio, audio, clock, and recording destination. No RF is sent.'):job.result?.ready?(job.result.sweep_type==='passive'?'Passive readiness passed. Start a passive sweep from Field sweeps within two minutes.':'Active readiness passed. Review and start from Field sweeps within two minutes.'):'Preflight needs attention. Open Field sweeps for corrective steps.';
    if(job.state==='completed'&&job.kind==='preview')message='Local preview ready. No radio connection or RF transmission.';
    if(job.state==='completed'&&job.kind==='capture')message=job.result?.decoded_samples===0?'Capture finished without received audio. Check the receiver and try again.':'Received audio saved. Choose it in step 3 to analyze.';
    if(job.state==='stopped'&&job.kind==='capture')message='Capture canceled early. Audio is saved for diagnosis; finish a timed capture to enable analysis.';
    if(job.state==='completed'&&job.kind==='analyze')message=job.result?.valid_windows?`${job.result.valid_windows} of ${job.result.window_count} valid windows. Open the report to inspect signal quality.`:'No valid tone windows. Open the report for reasons.';
    if(presentation){message=presentation.detail;c.badge.textContent=presentation.label;c.badge.className='badge '+presentation.tone;
      if(job.result?.automatic&&['waiting','receiving','transmitting'].includes(presentation.code))message+=' Clock: '+(job.result.clock_source||'checking GPS')+(job.result.clock_fallback?' ('+job.result.clock_fallback+')':'')+(job.result.next_tone_at?' · Next tone: '+timestamp(job.result.next_tone_at):'')+' · '+(job.result.tones_sent||0)+' tones completed.';
    }
    if(job.kind==='record'&&['stopped','completed'].includes(job.state))c.badge.textContent='Stopped';
    if(job.kind==='preflight'&&job.state==='completed'&&job.result?.ready===false){c.badge.textContent='Needs attention';c.badge.className='badge attention';}
    c.guidance.textContent=presentation?.guidance||(job.state==='failed'?'Saved results and diagnostics are retained. Review the error before trying again.':'');c.guidance.hidden=!c.guidance.textContent;
    if(job.result?.stop_verified===true&&job.kind==='transmit'&&(!activeStates.has(job.state)||presentation?.code==='receiving'))message+=' Radio TX was checked off.';
    if(job.kind==='survey'&&job.result?.mapped_points===0)message='Survey built with no qualified GPS points. Open it to inspect exclusions.';
    c.message.textContent=message;c.when.dateTime=job.created_at;c.when.textContent=timestamp(job.created_at)+(activeStates.has(job.state)?' · '+age(job.created_at)+' elapsed':'');c.log.textContent=job.log||'No diagnostic output.';
    const previous=previousJobStates.get(job.id);if(previous&&previous!==job.state&&['failed','unknown'].includes(job.state))notify(job.title+': '+message,true,true);previousJobStates.set(job.id,job.state);
    const actionSignature=JSON.stringify([job.state,job.files,job.result?.stop_verified,presentation?.code]);
    if(c.actionSignature!==actionSignature){
      c.actions.replaceChildren();c.media.replaceChildren();c.fileLinks.replaceChildren();
      if(['running','stopping'].includes(job.state))c.actions.append(button(job.state==='stopping'?'Stopping…':job.kind==='transmit'?(job.result?.automatic?'Halt automatic mode':'Stop tone'):job.kind==='capture'?'Cancel capture':'Stop',async()=>{await api('/api/stop',{id:job.id});await refresh();},job.kind==='transmit'?'danger':'secondary'));
      if(job.state==='unknown')c.actions.append(button('Confirm stopped',async()=>{if(window.confirm(job.kind==='transmit'?'Confirm that this operation has ended and the radio is not transmitting.':'Confirm that the prior recording or check process has ended. Saved data is retained.')){await api('/api/acknowledge',{id:job.id,confirmed:true});await refresh();}},'danger-outline'));
      if(profile&&(['running','unknown','failed'].includes(job.state))){if(job.kind==='record'||job.state==='unknown')c.actions.append(button('View radio',()=>showRadio(profile.id),'quiet'));else if(job.kind==='preflight'||job.result?.recording)c.actions.append(button('Field sweeps',()=>openFieldFor(profile,job),'quiet'));else if(['transmit','capture','services'].includes(job.kind))c.actions.append(button('Audio tests',()=>{location.hash='audio';},'quiet'));}
      const finished=!activeStates.has(job.state);
      if(finished&&job.files.includes('map.html'))c.actions.append(link('Open survey ↗',fileURL(job,'map.html'),'button primary'));
      if(finished&&job.files.includes('report.html'))c.actions.append(link('View report ↗',fileURL(job,'report.html'),'button primary'));
      if(finished&&job.files.length)c.actions.append(link('Download ZIP','/bundle/'+job.id));
      if(finished&&job.files.includes('tone.wav')){c.media.append(node('p','Listen locally · no RF','fine'));const audio=node('audio');audio.controls=true;audio.preload='none';audio.src=fileURL(job,'tone.wav');c.media.append(audio);}
      if(finished&&job.files.includes('audio.wav')){const audio=node('audio');audio.controls=true;audio.preload='none';audio.src=fileURL(job,'audio.wav');c.media.append(audio);}
      for(const file of job.files)if(finished)c.fileLinks.append(link(file,fileURL(job,file,true),'file-link'));
      c.downloads.hidden=!finished||!job.files.length;c.actionSignature=actionSignature;
    }
    for(const b of c.actions.querySelectorAll('button'))b.disabled=job.state==='stopping'||b.dataset.busy==='true';
    processDiscovery(job);
  }
  for(const [id,c] of jobCards)if(!shown.some(j=>j.id===id)){c.card.remove();jobCards.delete(id);}
  $('show-history').hidden=state.jobs.length<=5;setButton($('show-history'),history?'Show recent activity':'Show more history');
  const tx=state.jobs.find(j=>j.kind==='transmit'&&activeStates.has(j.state));$('transmit-banner').hidden=!tx;document.body.classList.toggle('has-transmit',!!tx);
  if(tx){$('transmit-banner-text').textContent=tx.state==='unknown'?'Previous transmit test needs attention. Check the radio.':tx.state==='stopping'?'Stopping tone. Waiting for radio TX verification…':(tx.result?.automatic?'Automatic mode active · ':'Transmit test active · ')+tx.title.replace('Transmit tone · ','');setButton($('stop-tone-banner'),tx.result?.automatic?'Halt automatic mode':'Stop tone',tx.state!=='running');$('stop-tone-banner').onclick=()=>act($('stop-tone-banner'),async()=>{await api('/api/stop',{id:tx.id});await refresh();});}
}
function processDiscovery(job){
  if(job.state!=='completed'||processedResults.has(job.id)||!job.result)return;
  if(job.kind==='scan'){
    $('scan-panel').hidden=false;$('scan-status').textContent=job.result.candidates.length+' candidates';$('scan-results').replaceChildren();
    for(const candidate of job.result.candidates){const row=node('div',undefined,'scan-row'),details=node('div');details.append(node('strong',candidate.name||'Unnamed radio'),node('small',candidate.address));row.append(details,button(savedRadio(candidate)?'Edit saved radio':'Use this radio',()=>openEditor(savedRadio(candidate),candidate)));$('scan-results').append(row);}
    if(!job.result.candidates.length)$('scan-results').append(node('p','No candidates found. Enable radio Bluetooth, move it nearby, close other control apps, and try again.','muted'));
    for(const warning of job.result.warnings||[])$('scan-results').append(node('p',warning,'scan-warnings'));
  }else if(job.kind==='services'){
    const count=job.result.filter(service=>service.audio).length;$('audio-support-result').textContent=count?`Found ${count} advertised audio service${count===1?'':'s'}. The test will select a supported audio connection.`:'No supported audio service was advertised. Check Classic Bluetooth pairing and radio firmware.';
  }
  processedResults.add(job.id);
}
$('show-history').onclick=()=>{history=!history;renderJobs();};

function openEditor(profile=null,candidate=null){
  if(profile&&activeFor(profile.id))throw new Error('Stop this radio’s active operation before editing its setup.');
  editing=profile?{...profile}:null;$('radio-dialog-title').textContent=profile?'Edit radio':'Add a radio';$('radio-form').reset();
  $('radio-name').value=profile?.name||'';$('radio-role').value=profile?.role||'mobile';$('radio-address').value=profile?.address||candidate?.address||'';
  $('radio-label').value=profile?.label||candidate?.name||'';$('radio-gps').checked=profile?profile.gps:true;$('radio-database').value=profile?.database||'';$('radio-port').value=profile?.port||'';
  $('radio-form-error').textContent='';$('radio-dialog').showModal();$('radio-name').focus();
}
$('add-radio').onclick=()=>openEditor();$('empty-add-radio').onclick=()=>openEditor();
$('radio-role').onchange=()=>{$('radio-gps').checked=$('radio-role').value==='mobile';};
$('radio-form').onsubmit=async event=>{
  event.preventDefault();const save=$('save-radio');save.disabled=true;$('radio-form-error').textContent='';
  const data={name:$('radio-name').value,role:$('radio-role').value,address:$('radio-address').value,label:$('radio-label').value,gps:$('radio-gps').checked};
  if(editing){data.id=editing.id;data.revision=editing.revision;data.advertised_name=editing.advertised_name;}
  if($('radio-database').value.trim())data.database=$('radio-database').value.trim();if($('radio-port').value)data.port=Number($('radio-port').value);
  try{await api('/api/profiles',data);$('radio-dialog').close();notify(data.name+' saved. You can start recording when the radio is ready.');await refresh();}
  catch(error){$('radio-form-error').textContent=error.message;}finally{save.disabled=false;}
};
for(const close of document.querySelectorAll('.close-dialog'))close.onclick=()=>close.closest('dialog').close();
$('dismiss-notice').onclick=()=>{$('notice').hidden=true;};
$('notice-activity').onclick=()=>$('activity').scrollIntoView({block:'start',behavior:'smooth'});
$('scan-radios').onclick=()=>act($('scan-radios'),async()=>{$('scan-panel').hidden=false;$('scan-status').textContent='Searching…';await launch({kind:'scan'},'Searching nearby radios. Keep radio Bluetooth enabled.');});

function toneData(){return {frequency:$('automatic-tone').checked?1000:Number($('tone-frequency').value),duration:$('automatic-tone').checked?5:Number($('tone-duration').value),level_dbfs:Number($('tone-level').value)};}
$('automatic-tone').onchange=()=>{for(const id of ['tone-frequency','tone-duration'])$(id).disabled=$('automatic-tone').checked;};
function renderAudioButtons(){
  const available=state.capabilities.audio&&state.capabilities.bluetooth&&!closing;
  const receiver=$('receiver-radio').value,transmitter=$('transmitter-radio').value;
  const receiverJob=receiver&&activeFor(receiver),transmitterJob=transmitter&&activeFor(transmitter);
  setButton($('start-capture'),receiverJob?.kind==='capture'?'Capture in progress':'Start receive capture',!available||!receiver||!!receiverJob);
  setButton($('check-receiver'),'Check audio support',!available||!receiver||!!receiverJob);
  setButton($('preview-tone'),'Create local preview',!state.capabilities.audio||closing);
  setButton($('review-transmit'),transmitterJob?'Radio busy':'Review RF transmission…',!available||!transmitter||!!transmitterJob);
  setButton($('analyze-audio'),'Analyze signal quality',!state.capabilities.audio||closing||!$('analysis-audio').value);
  $('audio-receiver-reason').textContent=closing?'The workspace is stopping. Allow cleanup to finish.':!available?'Install radio/audio support using the installation details above.':!receiver?'Choose a saved receiving radio.':receiverJob?'This radio is in use: '+radioPresentation(state.profiles.find(p=>p.id===receiver),receiverJob).label+'. Stop its operation before capturing audio.':'Receive capture opens this radio without transmitting.';
  $('audio-transmitter-reason').textContent=closing?'The workspace is stopping. Allow cleanup to finish.':!available?'RF tests require radio/audio support. Local preview remains available when audio tools are installed.':!transmitter?'Choose a transmitting radio.':transmitterJob?'This radio is in use: '+radioPresentation(state.profiles.find(p=>p.id===transmitter),transmitterJob).label+'. Use its stop control before starting another test.':'RF starts require review. For tones with RSSI/GPS recording, use Active sweep in Field sweeps.';
}
for(const id of ['receiver-radio','transmitter-radio','analysis-audio'])$(id).onchange=renderAudioButtons;
$('preview-tone').onclick=()=>act($('preview-tone'),()=>launch({kind:'preview',...toneData()},'Preparing a local tone preview. No RF will be sent.'));
$('start-capture').onclick=()=>act($('start-capture'),()=>launch({kind:'capture',profile:$('receiver-radio').value,duration:Number($('capture-duration').value)},'Starting receive-only capture. Wait for Capturing in Activity before sending the tone.'));
$('check-receiver').onclick=()=>act($('check-receiver'),()=>launch({kind:'services',profile:$('receiver-radio').value},'Checking the radio’s advertised Bluetooth audio services.'));
$('analyze-audio').onclick=()=>act($('analyze-audio'),()=>launch({kind:'analyze',audio:$('analysis-audio').value,frequency:Number($('tone-frequency').value)},'Analyzing the received tone. Results will appear in Activity.'));
$('review-transmit').onclick=()=>act($('review-transmit'),()=>reviewTransmission({profile:$('transmitter-radio').value,...toneData(),automatic:$('automatic-tone').checked}));
async function reviewTransmission(payload){
  intent=await api('/api/prepare-transmit',payload);
  $('transmit-review').replaceChildren();for(const [label,value] of [...(intent.survey_name?[['Survey session',intent.survey_name]]:[]),['Radio',intent.radio],['Address',intent.address],['Tone',intent.parameters.frequency+' Hz'],['Duration',intent.parameters.automatic?'5 seconds on, 15 seconds listening · repeats until halted':intent.parameters.duration+' seconds'],...(intent.parameters.automatic? [['Start',intent.parameters.automatic==='fixed'?'Next minute, then :00 / :20 / :40':'Next minute + 10 seconds, then :10 / :30 / :50'],['Clock','Radio GPS when available; computer clock otherwise']]:[]),...(intent.parameters.recording?[['Recording','RSSI and enabled GPS between tones']]:[]),['Digital level',intent.parameters.level_dbfs+' dBFS peak'],['RF settings','Current channel and power on the radio']])$('transmit-review').append(node('dt',label),node('dd',value));
  $('transmit-title').textContent=intent.parameters.automatic?'Review automatic mode':'Review this test tone';$('confirm-transmit').textContent=intent.parameters.automatic?'Start automatic mode':'Transmit test tone';$('transmit-confirm').checked=false;$('confirm-transmit').disabled=true;$('transmit-error').textContent='';$('transmit-dialog').showModal();
}
$('transmit-confirm').onchange=()=>{$('confirm-transmit').disabled=!$('transmit-confirm').checked;};
$('confirm-transmit').onclick=async()=>{
  const control=$('confirm-transmit');control.disabled=true;
  try{if(!$('transmit-confirm').checked)throw new Error('Confirm the radio settings first.');await api('/api/jobs',{kind:'transmit',intent:intent.intent,confirmed:true});$('transmit-dialog').close();if(intent.parameters.recording)location.hash='field';notify(intent.parameters.automatic?'Automatic mode armed. Use Halt automatic mode to stop.':'Transmit test started. Use Stop tone to request early cleanup.');await refresh();}
  catch(error){$('transmit-error').textContent=error.message;}finally{control.disabled=!$('transmit-confirm').checked;}
};

async function upload(file,kind,status,endpoint='/api/import'){
  if(!file)return;if(file.size>128*1024*1024){notify('Choose a file up to 128 MB.',true);return;}
  status.textContent='Importing '+file.name+'…';
  try{const response=await fetch(endpoint+'?kind='+kind,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-GPS-RSSI-Workspace':token,'X-File-Name':encodeURIComponent(file.name)},body:file});const result=await response.json();if(!response.ok)throw new Error(result.error||'Import failed');status.textContent='Imported '+file.name+'. Your original file is unchanged.';await refresh();}
  catch(error){status.textContent='';notify(error.message,true);}
}
$('import-database').onclick=()=>$('database-file').click();$('database-file').onchange=event=>upload(event.target.files[0],'database',$('database-import-status'));
$('import-audio').onclick=()=>$('audio-file').click();$('audio-file').onchange=event=>upload(event.target.files[0],'audio',$('audio-import-status'));
$('offline-mode').onchange=()=>act($('offline-mode'),async()=>{await api('/api/offline/mode',{offline:$('offline-mode').checked});await refresh();notify('Offline mode updated. Reopen surveys and reload live views.');});
$('export-offline-pack').onclick=()=>act($('export-offline-pack'),()=>launch({kind:'offline-export'},'Saving and pinning offline data. Download offline-data.zip from Activity.'));
$('fill-offline-data').onclick=()=>act($('fill-offline-data'),()=>launch({kind:'offline-fill'},'Filling queued missing data. Follow progress in Activity.'));
$('import-offline-map').onclick=()=>$('offline-map-file').click();
$('offline-map-file').onchange=event=>upload(event.target.files[0],'map',$('offline-import-status'),'/api/offline/import');
$('import-offline-terrain').onclick=()=>$('offline-terrain-file').click();
$('offline-terrain-file').onchange=async event=>{await upload(event.target.files[0],'terrain',$('offline-import-status'),'/api/offline/import');event.target.value='';};
$('remove-offline-terrain').onclick=()=>act($('remove-offline-terrain'),async()=>{await api('/api/offline/remove-terrain',{});await refresh();notify('Local terrain removed. Reopen terrain checks to update results.');});
$('import-offline-pack').onclick=()=>$('offline-pack-file').click();
$('offline-pack-file').onchange=event=>upload(event.target.files[0],'pack',$('offline-import-status'),'/api/offline/import');
function renderOffline(){
  const data=state.offline_data;if(!data)return;
  if($('offline-mode').dataset.busy!=='true')$('offline-mode').checked=data.offline;
  $('offline-summary').textContent=`${data.offline?'OFFLINE · saved data only':'Connected mode · requests allowed'} · ${data.tiles} cached basemap tiles · ${data.local_tiles} regional map tiles · ${data.elevations} queried elevation points · ${data.pending.tile||0} missing tiles · ${data.pending.elevation||0} missing elevations.`;
  $('offline-map-description').textContent=data.map?`${data.map.name||'Imported raster map'} · zooms ${data.map.minzoom}–${data.map.maxzoom}. ${data.map.attribution||''}`:'No regional MBTiles map imported.';
  $('offline-terrain-description').textContent=data.terrain?`${data.terrain.source} · ${data.terrain.cell_size_x_m} × ${data.terrain.cell_size_y_m} meter cells · ${data.terrain.width} × ${data.terrain.height} · NAVD88 meters. Used before cached or online elevations.`:'No local terrain imported.';
  setButton($('remove-offline-terrain'),'Remove local terrain',!data.terrain);
  setButton($('fill-offline-data'),'Fill missing data',data.offline||!Object.values(data.pending).some(n=>n>0)||state.jobs.some(j=>j.kind==='offline-fill'&&activeStates.has(j.state)));
  setButton($('export-offline-pack'),'Save offline pack',state.jobs.some(j=>j.kind==='offline-export'&&activeStates.has(j.state)));
}
let fieldCheckSignature='',lastStateReceivedAt=0,workspaceDisconnected=false;
function fieldSweepType(){const session=fieldSession();return session?(session.sweep_type||'active'):$('field-sweep-type').value;}
function alignUngroupedSweep(){if(fieldSession())return;const job=activeFor($('field-radio').value);if(job?.kind==='record')$('field-sweep-type').value='passive';else if(job?.kind==='transmit')$('field-sweep-type').value='active';}
function renderSweepChoice(){
  const session=fieldSession(),type=fieldSweepType(),passive=type==='passive',busy=activeFor($('field-radio').value);
  $('field-sweep-type').value=type;$('field-sweep-type').disabled=!!session||!!busy||closing;
  $('view-field').dataset.sweep=type;
  $('field-sweep-description').textContent=passive?'Record received signal strength and enabled GPS from existing RF on the current channel. This sweep sends no test tones.':'Transmit a 1 kHz tone for 5 seconds every 20 seconds and record the received signal between tones. Fixed and mobile stations use separate time slots.';
  if(session)$('field-sweep-description').textContent+=' Sweep type is fixed for this named session.';
  $('active-sweep-plan').hidden=passive;$('passive-sweep-plan').hidden=!passive;
  $('field-next-tone-panel').hidden=passive;$('field-tones-fact').hidden=passive;
  $('field-clock-label').textContent=passive?'Sample timestamps':'Timing clock';
}
$('field-sweep-type').onchange=()=>{try{localStorage.setItem('gps-rssi-sweep-type:'+state.folder,$('field-sweep-type').value);}catch{}renderField();};
function renderPassiveField(profile,job,result){
  const status=profile?radioPresentation(profile,job):null,live=job&&activeStates.has(job.state),current=live?status?.currentSample:null,other=profile&&activeFor(profile.id);
  let phase=!job?'Not started':job.state==='stopped'||job.state==='completed'?'Stopped':job.state==='failed'?'Sweep stopped':job.state==='unknown'?'Check operation':job.state==='stopping'?'Stopping…':'Passive · '+(status?.label||'connecting').toLowerCase();
  $('field-phase').textContent=phase;$('field-monitor').dataset.phase=status?.code||'idle';
  if(!job&&other)phase='Radio in use';$('field-phase').textContent=phase;
  $('field-monitor-detail').textContent=!job?(other?'This radio is in use by another operation. Stop that operation before starting this sweep.':'Check passive readiness, then start recording existing RF. No transmit review is needed.'):live?status.detail:job.state==='failed'?job.message:'Passive sweep ended. Saved samples are available in Surveys & exports.';
  $('field-stop-status').textContent=job?.state==='unknown'?'Operation stop unconfirmed':job?.state==='stopping'?'Stop requested':!job&&other?'Passive sweep not started':current?.is_in_tx?'Radio reports TX · sweep sends no tones':'This sweep sends no test tones';
  $('field-countdown').textContent='—';$('field-next-time').textContent='No transmit schedule';
  $('field-rssi').textContent=current?.is_in_rx&&!current.is_in_tx&&Number.isFinite(current.rssi)?current.rssi.toFixed(1):'—';
  const telemetry=result.latest_telemetry;
  $('field-telemetry').textContent=telemetry?'Last sample '+age(telemetry.received_at)+' ago':'Waiting for received signal samples';
  $('field-clock').textContent='Computer';$('field-clock-reason').textContent='Receive samples use computer receipt times; GPS fixes retain their own timestamps.';
  $('field-gps').textContent=!profile?'Not checked':!profile.gps?'Position recording off':!telemetry?'No position telemetry':current?(current.gps_locked?'Locked':'No lock'):'Last status: '+(telemetry.gps_locked?'locked':'no lock');
  $('field-samples').textContent=result.samples_recorded||0;$('field-tones').textContent='0';
  $('field-save-status').textContent=profile?'Passive recording: '+profile.database+' · Saved samples remain after stopping.':'Saved data stays on this computer.';
  setButton($('field-halt'),job?.state==='stopping'?'Stopping passive sweep…':'Stop passive sweep',!job||job.state!=='running');
}

function fieldSelection(){return state?.profiles.find(p=>p.id===$('field-radio').value);}
function fieldJob(profile){return state?.jobs.find(j=>j.profile===profile?.id&&sessionScope(j)&&(fieldSweepType()==='passive'?j.kind==='record':j.kind==='transmit'&&(j.result?.recording||j.title.startsWith('Field survey · ')||j.title.startsWith('Active sweep · '))));}
function fieldPreflight(profile){return state?.jobs.find(j=>j.profile===profile?.id&&sessionScope(j)&&j.kind==='preflight'&&(j.result?.sweep_type||(j.title.startsWith('Passive preflight · ')?'passive':'active'))===fieldSweepType());}
function fieldReady(profile){const check=fieldPreflight(profile),result=check?.result;return check?.state==='completed'&&result?.ready===true&&result.profile_revision===profile?.revision&&Date.now()-Date.parse(result.checked_at)>=0&&Date.now()-Date.parse(result.checked_at)<=120000;}
function applyFieldTheme(){let night=false;try{night=localStorage.getItem('gps-rssi-field-night')==='true';}catch{}$('view-field').classList.toggle('night',night);document.body.classList.toggle('field-night',location.hash==='#field'&&night);$('field-theme').setAttribute('aria-pressed',String(night));$('field-theme').textContent=night?'Daylight display':'Night display';}
$('field-theme').onclick=()=>{const night=!$('view-field').classList.contains('night');try{localStorage.setItem('gps-rssi-field-night',String(night));}catch{}$('view-field').classList.toggle('night',night);document.body.classList.toggle('field-night',location.hash==='#field'&&night);$('field-theme').setAttribute('aria-pressed',String(night));$('field-theme').textContent=night?'Daylight display':'Night display';};
$('field-focus').onclick=()=>{const fieldFocus=!document.body.classList.contains('field-focus');document.body.classList.toggle('field-focus',fieldFocus);$('field-focus').setAttribute('aria-pressed',String(fieldFocus));$('field-focus').textContent=fieldFocus?'Exit focus view':'Focus view';};
$('field-radio').onchange=()=>{try{localStorage.setItem('gps-rssi-field-radio:'+state.folder,$('field-radio').value);}catch{}alignUngroupedSweep();renderField();};
$('field-check').onclick=()=>act($('field-check'),()=>launch({kind:'preflight',profile:$('field-radio').value,...sessionPayload()},fieldSweepType()==='passive'?'Checking read-only radio control and the recording destination. No tone or audio connection is needed.':'Checking radio, audio, clock, and recording destination. No RF will be sent.'));
$('field-start').onclick=()=>act($('field-start'),()=>fieldSweepType()==='passive'?launch({kind:'record',profile:$('field-radio').value,field:true,...sessionPayload()},'Passive sweep started. Recording received RF; no test tones are sent.'):reviewTransmission({profile:$('field-radio').value,automatic:true,field:true,...sessionPayload()}));
$('field-halt').onclick=()=>act($('field-halt'),async()=>{const job=fieldJob(fieldSelection());if(job){await api('/api/stop',{id:job.id});await refresh();}});
function renderField(){
  if(!state)return;
  renderSweepChoice();const passive=fieldSweepType()==='passive';
  const profile=fieldSelection(),job=fieldJob(profile),check=fieldPreflight(profile),result=job?.result||{},current=profile&&activeFor(profile.id);
  const live=job&&activeStates.has(job.state),workspaceFresh=Date.now()-lastStateReceivedAt<6500,reportFresh=!live||!result.reported_at||Date.now()-Date.parse(result.reported_at)<15000,fresh=workspaceFresh&&reportFresh;
  $('field-role').textContent=profile?(passive?`${profile.role==='fixed'?'Fixed':'Mobile'} · ${profile.name} · Existing RF on the configured channel`:`${profile.role==='fixed'?'Fixed · :00 / :20 / :40':'Mobile · :10 / :30 / :50'} · ${profile.name}`):'Choose a radio to see its station role and timing.';
  setButton($('field-check'),check?.state==='running'?'Checking readiness…':passive?'Check passive readiness':'Check active readiness',!profile||!!(fieldSession()&&fieldSession().state!=='open')||(!passive&&!state.capabilities.audio)||!state.capabilities.bluetooth||!!current);
  setButton($('field-start'),passive?'Start passive sweep':'Review & start active sweep…',!profile||!!(fieldSession()&&fieldSession().state!=='open')||!state.capabilities.bluetooth||(!passive&&!state.capabilities.audio)||!fieldReady(profile)||!!current||!fresh);
  $('field-start-reason').textContent=!profile?'Choose a radio and run a preflight check.':fieldSession()&&fieldSession().state!=='open'?'This session is closed to new operations. Create a new session for another run.':!fresh?'Workspace status is unavailable. Wait for reconnection before starting.':!state.capabilities.bluetooth?'Install Bluetooth support using the installation details in Audio tests, then check again.':!passive&&!state.capabilities.audio?'Install audio support in Audio tests, then check again.':current?'This radio is in use. Stop its current sweep or operation before starting another.':fieldReady(profile)?(passive?'Passive preflight passed. Start receive-only sampling; no RF review or tones are needed.':'Active preflight passed. Confirm the radio channel and power to arm tones and recording.'):'Run a successful preflight. Checks expire after two minutes or a setup change.';
  const signature=JSON.stringify([check?.id,check?.state,check?.result]);
  if(signature!==fieldCheckSignature){$('field-checks').replaceChildren();for(const item of check?.result?.checks||[]){const row=node('div',undefined,'field-check-row '+item.state);row.append(node('strong',item.name+' · '+(item.state==='ready'?'Ready':item.state==='attention'?'Needs attention':'Unavailable')),node('p',item.detail));$('field-checks').append(row);}if(check?.state==='failed')$('field-checks').append(node('p',check.message,'form-error'));fieldCheckSignature=signature;}
  if(passive){renderPassiveField(profile,job,result);return;}
  let phase=!job?'Not started':job.state==='unknown'?'Check radio':job.state==='stopping'?'Stopping…':job.state==='failed'?'Survey halted':job.state==='stopped'||job.state==='completed'?'Stopped':result.state==='transmitting'?'TX · sending tone':result.state==='receiving'?'RX · listening':result.state==='waiting'?'Armed · waiting':'Connecting…';
  if(live&&!fresh)phase=workspaceFresh?'Status stale':'Connection lost';
  $('field-phase').textContent=phase;$('field-monitor').dataset.phase=!fresh||job?.state==='unknown'?'unknown':result.state||'idle';
  $('field-monitor-detail').textContent=live&&!fresh?(workspaceFresh?'The radio session has stopped reporting status. Its current TX state is unknown.':'Cannot reach the workspace. The radio may still be transmitting; its current state is unknown.'):job?.state==='stopping'?'Stop requested. Allow cleanup and TX verification to finish.':job?.state==='unknown'?'TX stop is not verified. Check the radio and open its Activity details.':job?.state==='failed'?job.message:live?'Repeating 1 kHz tones. Signal samples are saved during quiet intervals.':job?'Survey ended. Saved samples are available in Surveys & exports.':'Check readiness, review the radio settings, then start the survey.';
  $('field-stop-status').textContent=live&&!fresh?'Radio state unknown':job?.state==='unknown'?'TX not verified off':job?.state==='stopping'?'Stop requested':result.stop_verified===true?'TX verified off':result.state==='transmitting'?'TX stop pending':job?'No tone sent yet':'No transmission requested';
  const next=Date.parse(result.next_tone_host_at),count=(next-Date.now())/1000;
  $('field-countdown').textContent=live&&fresh&&job.state==='running'&&Number.isFinite(count)&&result.state!=='transmitting'?(count>0?Math.ceil(count)+'s':'Due'):'—';
  $('field-next-time').textContent=live&&job.state==='running'&&result.next_tone_at?'Scheduled: '+timestamp(result.next_tone_at)+' (timing clock)':'No upcoming tone';
  const telemetry=result.latest_telemetry,ageMs=telemetry?Date.now()-Date.parse(telemetry.received_at):Infinity;
  $('field-rssi').textContent=live&&job.state==='running'&&fresh&&ageMs<5000&&telemetry?.is_in_rx&&!telemetry.is_in_tx&&result.state!=='transmitting'?Number(telemetry.rssi).toFixed(1):'—';
  $('field-telemetry').textContent=telemetry?'Last sample '+age(telemetry.received_at)+' ago':'Waiting for signal samples';
  $('field-clock').textContent=result.clock_source||check?.result?.clock_source||'Not checked';
  $('field-clock-reason').textContent=result.clock_source?(result.clock_fallback||''):(check?.result?.clock_fallback||'');
  $('field-gps').textContent=!profile?'Not checked':!profile.gps?'Position recording off':!telemetry?'No position telemetry':ageMs>=5000||!live?'Last status: '+(telemetry.gps_locked?'locked':'no lock'):telemetry.gps_locked?'Locked':'No lock';
  $('field-samples').textContent=result.samples_recorded||0;$('field-tones').textContent=result.tones_sent||0;
  $('field-save-status').textContent=profile?'Recording: '+profile.database+' · Saved samples remain after halt.':'Saved data stays on this computer.';
  setButton($('field-halt'),job?.state==='stopping'?'Stopping · verifying TX…':'Halt automatic mode',!job||job.state!=='running');
}
applyFieldTheme();
setInterval(()=>{if(state&&!state.closed&&['','#radios','#live'].includes(location.hash)){renderRadios();if(location.hash==='#live')renderLive();}},1000);
setInterval(()=>{if(state&&location.hash==='#field'){renderField();renderSessionActions();}},250);

let sessionLibrarySignature='';
function fieldSession(){return (state?.sessions||[]).find(s=>s.id===$('field-session').value);}
function sessionPayload(){return {sweep_type:fieldSweepType(),...(fieldSession()?{survey_session:fieldSession().id}:{})};}
function sessionPhase(session){return session.result?.state==='unknown'||session.operations?.some(op=>op.state==='unknown')?'Needs attention':session.state==='finishing'?'Stopping radios':session.state==='attention'?'Needs attention':session.result?.state==='completed'?'Results ready':session.result?.state==='running'?'Preparing results':['failed','stopped','interrupted'].includes(session.result?.state)?'Results interrupted':session.state==='finished'?'Finished':'Open';}
function sessionScope(job){return (job.survey_session||'')===$('field-session').value;}
function selectSession(id){$('field-session').value=id;try{localStorage.setItem('gps-rssi-session:'+state.folder,id);}catch{}renderSessionRadios();alignUngroupedSweep();renderField();renderSessionActions();}
function renderSessionRadios(){
  const session=fieldSession(),members=new Set(session?.radios.map(p=>p.id));
  const profiles=state.profiles.filter(p=>!p.error&&(!session||members.has(p.id)));
  chooseOptions($('field-radio'),profiles,'Choose a saved radio');
  if(!$('field-radio').value){let saved;try{saved=localStorage.getItem('gps-rssi-field-radio:'+state.folder);}catch{}if(profiles.some(p=>p.id===saved))$('field-radio').value=saved;else if(profiles.length)$('field-radio').value=profiles[0].id;}
}
$('field-session').onchange=()=>selectSession($('field-session').value);
$('new-session').onclick=()=>{
  $('session-sweep-type').value=fieldSweepType();$('session-name').value='';$('session-notes').value='';$('session-form-error').textContent='';$('session-radio-list').replaceChildren();
  for(const profile of state.profiles.filter(p=>!p.error)){const label=node('label',undefined,'checkbox'),input=node('input');input.type='checkbox';input.value=profile.id;input.checked=profile.id===$('field-radio').value;label.append(input,node('span',profile.name+' · '+profile.role+(profile.label?' · '+profile.label:'')));$('session-radio-list').append(label);}
  $('session-dialog').showModal();$('session-name').focus();
};
$('session-form').onsubmit=async event=>{
  event.preventDefault();const submit=event.submitter;submit.disabled=true;
  try{const session=await api('/api/sessions',{name:$('session-name').value,sweep_type:$('session-sweep-type').value,notes:$('session-notes').value,profiles:[...$('session-radio-list').querySelectorAll('input:checked')].map(input=>input.value)});await refresh();selectSession(session.id);$('session-dialog').close();notify(session.sweep_type==='passive'?'Passive session created. Check readiness, then start recording existing RF.':'Active session created. Check readiness and review each radio before transmitting.');}
  catch(error){$('session-form-error').textContent=error.message;}finally{submit.disabled=false;}
};
async function finishSurvey(id){await api('/api/sessions/finish',{id});await refresh();notify('Finishing this session. Waiting for radio cleanup before preparing results.');}
$('finish-session').onclick=()=>act($('finish-session'),()=>finishSurvey(fieldSession().id));
function renderSessionActions(){
  const session=fieldSession(),result=session?.result,ready=result?.state==='completed'&&result.files.includes('index.html');
  $('field-session-description').textContent=session?`${session.name} · ${(session.sweep_type||'active').toUpperCase()} SWEEP · ${sessionPhase(session)} · ${session.radios.map(p=>p.name).join(', ')}${session.notes?'\n'+session.notes:''}`:'Create a named session to group radios, notes, recordings, and results. Ungrouped runs remain available.';
  $('field-session-monitor').textContent=session?session.name+' · '+(session.sweep_type||'active')+' sweep · '+sessionPhase(session):'';
  $('finish-session').hidden=!session||ready;
  const resultBusy=result&&activeStates.has(result.state);
  setButton($('finish-session'),session?.state==='finishing'?'Stopping session radios…':session?.state==='attention'?'Retry finish survey':['failed','stopped','interrupted'].includes(result?.state)?'Retry results':resultBusy?'Preparing results…':'Finish survey',!session||session.state==='finishing'||!!resultBusy);
  $('session-finish-status').textContent=session?session.state==='open'?'Finish survey stops only operations belonging to this session, then prepares its results.':session.result?.state==='unknown'?'Result generation was interrupted. Confirm the prior operation ended in Activity, then retry.':['failed','stopped','interrupted'].includes(session.result?.state)?'Results could not be built. Saved recordings are retained. '+session.result.message:session.message:'';
  $('session-open-results').hidden=!ready;if(ready)$('session-open-results').href=fileURL(result,'index.html');
}
function renderSessions(){
  const sessions=state.sessions||[],previous=$('field-session').value;
  chooseOptions($('field-session'),sessions.map(s=>({id:s.id,name:s.name+' · '+(s.sweep_type||'active')+' sweep · '+sessionPhase(s)})),'Ungrouped run');
  if(!$('field-session').dataset.restored){try{const savedMode=localStorage.getItem('gps-rssi-sweep-type:'+state.folder);if(['passive','active'].includes(savedMode))$('field-sweep-type').value=savedMode;const saved=localStorage.getItem('gps-rssi-session:'+state.folder);if(sessions.some(s=>s.id===saved))$('field-session').value=saved;}catch{}$('field-session').dataset.restored='true';}
  if(previous&&sessions.some(s=>s.id===previous))$('field-session').value=previous;
  renderSessionRadios();alignUngroupedSweep();renderSweepChoice();renderSessionActions();
  const signature=JSON.stringify(sessions);if(signature===sessionLibrarySignature)return;sessionLibrarySignature=signature;
  $('session-library').replaceChildren();$('sessions-empty').hidden=!!sessions.length;
  for(const session of sessions){const card=node('article',undefined,'session-card'),header=node('div',undefined,'section-heading');card.dataset.session=session.id;header.append(node('h3',session.name+' · '+(session.sweep_type||'active')+' sweep'),node('span',sessionPhase(session),'badge'));card.append(header,node('p',timestamp(session.created_at)+' · '+session.radios.map(p=>p.name).join(', ')),node('p',session.samples_recorded+' saved samples'+((session.sweep_type||'active')==='active'?' · '+session.tones_sent+' completed tones':'')));if(session.notes)card.append(node('p',session.notes));
    const actions=node('div',undefined,'actions');actions.append(button('View session',()=>{selectSession(session.id);location.hash='field';}));
    if(session.result?.state==='completed'&&session.result.files.includes('index.html')){actions.append(link('Open results ↗',fileURL(session.result,'index.html'),'button primary'),link('Download survey ZIP','/bundle/'+session.result.id));}
    else if(session.state!=='finishing'&&!(session.result&&activeStates.has(session.result.state)))actions.append(button(['failed','stopped','interrupted'].includes(session.result?.state)?'Retry results':'Finish survey',()=>finishSurvey(session.id),'quiet'));
    if(session.state==='attention'||['failed','stopped','interrupted','unknown'].includes(session.result?.state))card.append(node('p',['failed','stopped','interrupted','unknown'].includes(session.result?.state)?session.result.message:session.message,'form-error'));
    card.append(actions);$('session-library').append(card);
  }
}

let resultsData={results:[],comparisons:[]},resultsLoaded=false,resultsBusy=false,resultsError='',resultsFingerprint='';
let selectedResults=[];
const resultCards=new Map();
function resultFilters(){return {query:$('results-search').value,type:$('results-type').value,radio:$('results-radio').value,from:$('results-from').value,to:$('results-to').value};}
function selectedRows(){return selectedResults.map(id=>resultsData.results.find(row=>row.id===id)).filter(Boolean);}
function updateResultSelections(){
  for(const [id,item] of resultCards){const row=item.row,index=selectedResults.indexOf(id),limited=index<0&&selectedResults.length>=2;
    item.checkbox.checked=index>=0;item.checkbox.disabled=!row.compare_available||limited||resultsBusy||closing;
    item.choiceText.textContent=!row.compare_available?(row.comparison_reason||row.problem||'Comparison needs mapped mobile receive points'):index>=0?'Compare as '+(index===0?'A':'B'):limited?'Compare · two sweeps already selected':'Select for comparison';
  }
}
function renderResultComparisonTray(){
  const rows=selectedRows();$('results-selections').replaceChildren();
  for(let i=0;i<2;i++){const row=rows[i],card=node('div',undefined,'result-selection');card.append(node('strong',(i?'B · ':'A · ')+(row?row.name:'Choose a sweep')));if(row){card.append(node('p',row.sweep_type.toUpperCase()+' sweep'));card.append(button('Remove '+(i?'B':'A'),()=>{selectedResults=selectedResults.filter(id=>id!==row.id);updateResultSelections();renderResultComparisonTray();},'quiet'));}$('results-selections').append(card);}
  const visible=new Set(ResultsModel.filtered(resultsData.results,resultFilters()).map(row=>row.id)),hidden=rows.filter(row=>!visible.has(row.id)).length;
  $('results-selection-count').textContent=rows.length+' of 2 selected'+(hidden?' · '+hidden+' hidden by filters':'');
  const mixed=ResultsModel.mixed(rows);document.querySelector('.result-compare').classList.toggle('mixed',mixed);
  $('results-comparison-note').textContent=mixed?'Passive vs Active: ambient traffic and controlled tones represent different test conditions. Sweep types stay visible in the comparison.':rows.length===2?'Comparison matches nearby measurements with matching frequencies. Each receiver retains its relative RSSI scale.':'Select two sweeps with mapped mobile receive points. A is the reference; differences are B minus A.';
  setButton($('compare-results'),'Compare selected sweeps',rows.length!==2||rows.some(row=>!row.compare_available)||resultsBusy);
  setButton($('clear-results-selection'),'Clear selection',!rows.length);
}
function resultCard(row){
  const card=node('article',undefined,'result-card'),header=node('div',undefined,'section-heading'),heading=node('h2',row.name);
  card.dataset.result=row.id;header.append(heading,node('span',row.sweep_type.toUpperCase()+' SWEEP','result-type'));card.append(header);
  card.append(node('p',timestamp(ResultsModel.recordedAt(row))+' · '+row.radios.map(r=>r.name).join(', '),'muted'));
  if(row.notes)card.append(node('p',row.notes,'result-notes'));
  const quality=row.quality,metrics=node('dl',undefined,'result-metrics');
  const interruptions=quality?quality.connection_interruptions+' reported'+(quality.unrecovered_interruptions?' · '+quality.unrecovered_interruptions+' unrecovered':''):'Unavailable';
  for(const [label,value] of [['Sample span',ResultsModel.duration(quality?.duration_seconds)],['Saved samples',quality?.sample_count??'Unavailable'],['Mobile GPS coverage',ResultsModel.gps(quality)],['Link interruptions',interruptions]]){const cell=node('div');cell.append(node('dt',label),node('dd',value));metrics.append(cell);}card.append(metrics);
  if(quality?.failed_operations)card.append(node('p',quality.failed_operations+' operation(s) ended early. Collected data is retained.','result-quality-note'));
  if(row.problem)card.append(node('p',row.problem,'form-error'));
  const choice=node('label',undefined,'checkbox compare-choice'),checkbox=node('input'),choiceText=node('span');checkbox.type='checkbox';checkbox.setAttribute('aria-label','Select '+row.name+' for comparison');
  checkbox.onchange=()=>{if(checkbox.checked){if(selectedResults.length<2)selectedResults.push(row.id);}else selectedResults=selectedResults.filter(id=>id!==row.id);updateResultSelections();renderResultComparisonTray();};choice.append(checkbox,choiceText);card.append(choice);
  const actions=node('div',undefined,'actions'),files=row.result.files;
  if(files.includes('map.html'))actions.append(link('Open map ↗',fileURL(row.result,'map.html'),'button primary'));
  if(files.includes('index.html'))actions.append(link('Details ↗',fileURL(row.result,'index.html')));
  if(files.length)actions.append(link('Download ZIP','/bundle/'+row.result.id));
  if(files.includes('measurements.csv'))actions.append(link('CSV',fileURL(row.result,'measurements.csv',true),'button quiet'));
  card.append(actions);
  if(quality){const detail=node('details');detail.append(node('summary','Quality details'),node('p','Sample span is first to last saved sample, including interruptions. '+quality.gps_points+' qualified map points from '+quality.mobile_receive_samples+' mobile receive samples. '+quality.recovered_interruptions+' recovered connection interruption(s), with '+ResultsModel.duration(quality.recovered_gap_seconds)+' of reported recovery time. Scheduled tone waits and quiet RF are not counted as connection outages.'));card.append(detail);}
  return {card,checkbox,choiceText,row,signature:JSON.stringify(row)};
}
function renderSavedComparisons(){
  $('results-comparisons').replaceChildren();$('results-comparisons-empty').hidden=!!resultsData.comparisons.length;
  for(const job of resultsData.comparisons){const card=node('article',undefined,'comparison-result');card.dataset.comparison=job.id;card.append(node('h3',job.title),node('p',timestamp(job.created_at)+' · '+({running:'Preparing comparison…',completed:'Ready',failed:'Failed',unknown:'Needs attention',stopped:'Stopped',stopping:'Stopping…',interrupted:'Interrupted'}[job.state]||job.state)));
    const runs=job.result?.comparison?.runs;if(runs)card.append(node('p',runs.map((r,i)=>(i?'B: ':'A: ')+r.name+' · '+r.sweep_type+' sweep').join(' ↔ ')));
    if(job.result?.comparison?.mixed_sweep_types)card.append(node('p','Passive and Active sweeps represent different test conditions.','result-quality-note'));
    const actions=node('div',undefined,'actions');
    if(job.state==='completed'&&job.files.includes('map.html'))actions.append(link('Open comparison ↗',fileURL(job,'map.html')+'#side-by-side-heading','button primary'),link('Download comparison ZIP','/bundle/'+job.id));
    if(['failed','unknown','interrupted'].includes(job.state)){card.append(node('p',job.message,'form-error'));if(state.jobs.some(item=>item.id===job.id))actions.append(button('View operation',()=>showOperation(job.id),'quiet'));}
    card.append(actions);$('results-comparisons').append(card);
  }
}
function renderResults(){
  if(!state)return;
  const rows=ResultsModel.filtered(resultsData.results),radios=new Map();for(const row of rows)for(const radio of row.radios){const id=ResultsModel.radioKey(radio);if(!radios.has(id))radios.set(id,{id,name:radio.name+' · '+radio.role});}
  chooseOptions($('results-radio'),[...radios.values()],'All radios');
  const filters=resultFilters(),visible=ResultsModel.filtered(rows,filters),shown=new Set(visible.map(row=>row.id));
  for(const [index,row] of rows.entries()){let item=resultCards.get(row.id);if(!item||item.signature!==JSON.stringify(row)){const fresh=resultCard(row);if(item)item.card.replaceWith(fresh.card);else $('results-list').append(fresh.card);resultCards.set(row.id,fresh);item=fresh;}item.card.hidden=!shown.has(row.id);if($('results-list').children[index]!==item.card)$('results-list').insertBefore(item.card,$('results-list').children[index]||null);}
  for(const [id,item] of resultCards)if(!rows.some(row=>row.id===id)){item.card.remove();resultCards.delete(id);}
  $('results-status').textContent=resultsError?'Could not refresh results. The prior listing is retained. '+resultsError:!resultsLoaded?'Loading completed sweeps…':filters.from&&filters.to&&filters.from>filters.to?'Recorded from must be on or before Through.':'Showing '+visible.length+' of '+rows.length+' completed sweeps.';
  $('results-empty').hidden=!resultsLoaded||!!visible.length;
  $('results-empty').querySelector('h2').textContent=rows.length?'No sweeps match these filters':'No completed sweeps yet';
  $('results-empty').querySelector('p').textContent=rows.length?'Clear or adjust the filters. Your comparison selections are retained above.':'Finish a named session in Field sweeps to prepare its results.';
  setButton($('refresh-results'),resultsBusy?'Refreshing…':'Refresh results',resultsBusy);
  updateResultSelections();renderResultComparisonTray();renderSavedComparisons();
}
async function refreshResults(){
  if(!state||resultsBusy)return;resultsBusy=true;resultsError='';renderResults();
  try{const response=await fetch('/api/results',{cache:'no-store'});if(!response.ok)throw new Error('The completed-results catalog is unavailable.');const data=await response.json();resultsData=data;resultsLoaded=true;selectedResults=selectedResults.filter(id=>data.results.some(row=>row.id===id&&row.compare_available));}
  catch(error){resultsError=error.message;}
  finally{resultsBusy=false;renderResults();}
}
function resultsGate(){if(location.hash!=='#results')return;const signature=JSON.stringify([state.sessions?.map(s=>[s.id,s.state,s.result?.id,s.result?.state,s.result?.files]),state.jobs.filter(j=>j.kind==='comparison').map(j=>[j.id,j.state,j.files])]);if(signature!==resultsFingerprint&&!resultsBusy){resultsFingerprint=signature;refreshResults();}}
for(const id of ['results-search','results-type','results-radio','results-from','results-to'])$(id).addEventListener(id==='results-search'?'input':'change',renderResults);
$('clear-result-filters').onclick=()=>{for(const id of ['results-search','results-type','results-radio','results-from','results-to'])$(id).value='';renderResults();};
$('clear-results-selection').onclick=()=>{selectedResults=[];updateResultSelections();renderResultComparisonTray();};
$('refresh-results').onclick=()=>refreshResults();
$('compare-results').onclick=()=>act($('compare-results'),async()=>{if(selectedResults.length!==2)throw new Error('Select two sweeps first.');const job=await api('/api/jobs',{kind:'comparison',sessions:[...selectedResults]});$('results-compare-status').textContent=job.state==='completed'?'Comparison ready. Open it from Saved comparisons below.':'Preparing comparison from completed snapshots. Open it from Saved comparisons when ready.';await refresh();await refreshResults();});
window.addEventListener('hashchange',()=>{if(state&&location.hash==='#results')refreshResults();});

function openExit(){$('exit-dialog').showModal();}
$('exit-workspace').onclick=openExit;if($('exit-workspace-mobile'))$('exit-workspace-mobile').onclick=openExit;
$('confirm-exit').onclick=()=>act($('confirm-exit'),async()=>{await api('/api/exit',{confirmed:true});closing=true;$('exit-dialog').close();notify('Stopping managed operations and saving data. Allow radio cleanup to finish.');await refresh();});

function render(){
  renderRadios();renderDatasets();renderSessions();renderJobs();renderLive();renderOffline();renderField();resultsGate();
  $('workspace-folder').textContent='App folder: '+state.folder;$('audio-unavailable').hidden=state.capabilities.audio&&state.capabilities.bluetooth;
  renderAudioButtons();
  $('connection-state').textContent=state.closed?'Workspace closed':closing?'Stopping operations…':'Local workspace online';
  if(state.closed){closing=true;clearTimeout(pollTimer);const unsafe=state.jobs.some(j=>activeStates.has(j.state));notify(unsafe?'Workspace closed with a radio warning. Check the radio and its operation details.':'Workspace closed. Managed operations have ended and saved data is retained.',unsafe);}
}
async function refresh(){
  const ticket=++serial,response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw new Error('The local workspace is unavailable.');
  const data=await response.json();if(ticket<applied)return;applied=ticket;state=data;lastStateReceivedAt=Date.now();token=data.token;closing=closing||data.closing;render();
}
async function poll(){
  try{await refresh();if(workspaceDisconnected){notify('Workspace reconnected. Check the radio status before continuing.');workspaceDisconnected=false;}}catch(error){workspaceDisconnected=true;$('connection-state').textContent='Connection lost · retrying';if(!closing)notify('Cannot reach the local workspace. Existing operations may still be running. Reconnecting…',true);}
  if(!state?.closed)pollTimer=setTimeout(poll,2000);
}
poll();
