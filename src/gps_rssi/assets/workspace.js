'use strict';
const $=id=>document.getElementById(id);
const activeStates=new Set(['running','stopping','unknown']);
const labels={radios:'Radios',live:'Live view',surveys:'Surveys & exports',audio:'Audio tests'};
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
  $('current-view').textContent=labels[current];
}
window.addEventListener('hashchange',view);view();
function age(value){const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(value))/1000));if(!Number.isFinite(seconds))return 'unknown';return seconds<60?seconds+'s':seconds<3600?Math.floor(seconds/60)+' min':Math.floor(seconds/3600)+' hr';}
function timestamp(value){return new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});}
function activeFor(profile){return state.jobs.find(j=>j.profile===profile&&activeStates.has(j.state));}
function chooseOptions(select,items,placeholder){
  const signature=JSON.stringify(items);if(select.dataset.signature===signature)return;
  const previous=select.value;select.replaceChildren();const empty=node('option',placeholder);empty.value='';select.append(empty);
  for(const item of items){const option=node('option',item.name);option.value=item.id;select.append(option);}
  if(items.some(item=>item.id===previous))select.value=previous;
  select.dataset.signature=signature;
}
async function launch(data,message){const result=await api('/api/jobs',data);notify(message||'Started. Follow progress in Activity.',false,true);await refresh();const current=state.jobs.find(j=>j.id===result.id);if(current&&['failed','unknown'].includes(current.state))notify(current.message,true,true);return result;}

function createRadioCard(profile){
  const card=node('article',undefined,'radio-card');card.dataset.profile=profile.id;
  const top=node('div',undefined,'radio-top'),heading=node('div'),name=node('h3',undefined,'radio-name'),role=node('p',undefined,'radio-role'),badge=node('span',undefined,'badge');
  heading.append(name,role);top.append(heading,badge);
  const notes=node('p',undefined,'radio-notes'),stats=node('div',undefined,'radio-stats'),signal=node('div'),signalValue=node('strong'),signalLabel=node('small','Receive RSSI /100'),meter=node('div',undefined,'signal-meter'),fill=node('span');meter.append(fill);signal.append(signalValue,signalLabel,meter);
  const gps=node('div'),gpsValue=node('strong'),gpsLabel=node('small','GPS lock');gps.append(gpsValue,gpsLabel);stats.append(signal,gps);
  const freshness=node('p',undefined,'radio-freshness'),actions=node('div',undefined,'radio-actions');
  const start=button('Start recording',async()=>{const job=activeFor(profile.id);if(job&&job.kind==='record'){await api('/api/stop',{id:job.id});await refresh();}else await launch({kind:'record',profile:profile.id},'Recorder started. Check for recent telemetry on its radio card.');});
  start.className='primary';
  const live=button('Live view',()=>{chooseLive(profile.id);location.hash='live';},'secondary');
  const share=button('Snapshot',()=>launch({kind:'share',database:'profile:'+profile.id},'Creating a portable recording. Download it from Activity.'),'quiet');
  const edit=button('Edit',()=>openEditor(state.profiles.find(p=>p.id===profile.id)),'quiet edit-radio');
  actions.append(start,live,share);card.append(top,notes,stats,freshness,actions,edit);
  $('radio-list').append(card);
  return {card,name,role,badge,notes,signalValue,gpsValue,gpsLabel,fill,freshness,start,live,share,edit};
}
function renderRadios(){
  $('configured-count').textContent=String(state.profiles.filter(p=>!p.error).length);
  $('recording-count').textContent=String(state.jobs.filter(j=>j.kind==='record'&&j.state==='running').length);
  $('saved-count').textContent=String(state.datasets.databases.length);
  $('radios-empty').hidden=state.profiles.length>0;
  for(const profile of state.profiles){
    if(!radioCards.has(profile.id))radioCards.set(profile.id,createRadioCard(profile));
    const c=radioCards.get(profile.id),job=activeFor(profile.id),latest=profile.latest;
    c.name.textContent=profile.name;c.role.textContent=profile.error?'Setup needs attention':`${profile.role==='fixed'?'Fixed station':'Mobile station'} · ${profile.gps?'GPS enabled':'GPS off'}`;
    c.notes.textContent=profile.error||profile.label||'Add model and antenna notes in this radio’s setup.';
    const badge=profile.error?'failed':job?job.state:'ready';c.badge.className='badge '+badge;
    c.badge.textContent=profile.error?'Needs setup':job?job.state==='unknown'?'Check radio':job.state==='stopping'?'Stopping':job.kind==='record'?'Recording':job.kind==='capture'?'Capturing':'Audio task':'Ready';
    const hasSignal=latest&&latest.is_in_rx&&!latest.is_in_tx&&latest.rssi_unit==='benshi_relative_0_100'&&Number.isFinite(latest.rssi_value);
    c.signalValue.textContent=hasSignal?latest.rssi_value.toFixed(1):'—';c.fill.style.width=hasSignal?Math.max(0,Math.min(100,latest.rssi_value))+'%':'0%';
    c.gpsValue.textContent=!profile.gps?'Off':latest?.gps_locked?'Locked':'Waiting';
    c.freshness.textContent=profile.data_error||(!latest?job?.kind==='record'?'Recorder started · waiting for telemetry':'No recorded telemetry yet':`Latest telemetry ${age(latest.received_at)} ago · ${latest.is_in_tx?'TX':latest.is_in_rx?'RX':'Idle'}`);
    setButton(c.start,job?.kind==='record'?(job.state==='stopping'?'Stopping…':'Stop recording'):'Start recording',!!profile.error||!state.capabilities.bluetooth||(!!job&&(job.kind!=='record'||job.state!=='running')));
    setButton(c.live,'Live view',!job||job.kind!=='record'||job.state!=='running');
    setButton(c.share,'Snapshot',!profile.has_data);setButton(c.edit,'Edit',!!job||!!profile.error||!state.capabilities.setup);
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
$('build-survey').onclick=()=>act($('build-survey'),()=>launch({kind:'survey',databases:selectedDatabases(),source:$('survey-source').value},'Building your shared survey. The map and GIS files will appear in Activity.'));
$('merge-recordings').onclick=()=>act($('merge-recordings'),()=>launch({kind:'merge',databases:selectedDatabases()},'Combining recordings without duplicate samples.'));
$('export-file').onclick=()=>act($('export-file'),()=>launch({kind:'export',database:$('export-database').value,format:$('export-format').value,source:$('survey-source').value},'Creating your export. Download it from Activity.'));
$('share-recording').onclick=()=>act($('share-recording'),()=>launch({kind:'share',database:$('export-database').value},'Creating a standalone snapshot, including committed recording data.'));

function fileURL(job,name,download=false){return '/files/'+encodeURIComponent(job.id)+'/'+encodeURIComponent(name)+(download?'?download=1':'');}
function link(label,url,className='button secondary'){const a=node('a',label,className);a.href=url;a.target='_blank';a.rel='noopener';return a;}
function createJob(job){
  const card=node('article',undefined,'job'),heading=node('div',undefined,'job-header'),title=node('h3'),badge=node('span',undefined,'badge');heading.append(title,badge);
  const message=node('p'),when=node('time'),actions=node('div',undefined,'actions'),media=node('div'),downloads=node('details'),downloadTitle=node('summary','Download files'),fileLinks=node('div',undefined,'file-links');downloads.append(downloadTitle,fileLinks);
  const details=node('details'),summary=node('summary','Operation details'),log=node('pre');details.append(summary,log);
  card.dataset.job=job.id;card.append(heading,message,when,actions,media,downloads,details);$('activity-list').prepend(card);
  return {card,title,badge,message,when,actions,media,downloads,fileLinks,details,log,actionSignature:''};
}
function renderJobs(){
  const active=state.jobs.filter(j=>activeStates.has(j.state));$('active-count').textContent=active.length+' active';$('activity-empty').hidden=state.jobs.length>0;
  const shown=state.jobs.filter((j,i)=>history||i<5||activeStates.has(j.state));
  for(const job of [...shown].reverse()){
    if(!jobCards.has(job.id))jobCards.set(job.id,createJob(job));const c=jobCards.get(job.id);
    c.title.textContent=job.title;c.badge.className='badge '+job.state;c.badge.textContent={running:'In progress',stopping:'Stopping',completed:'Ready',stopped:'Stopped',failed:'Failed',unknown:'Check radio',interrupted:'Reviewed'}[job.state]||job.state;
    let message=job.message;
    if(job.state==='running')message=job.kind==='record'?'Recording is running. Check the radio card for recent telemetry.':job.kind==='transmit'?(job.result?.state==='transmitting'?'Sending the test tone.':'Connecting to the transmitter…'):job.kind==='capture'?(job.result?.state==='capturing'?'Capturing received audio…':'Connecting to the receiver…'):job.kind==='scan'?'Searching for nearby Bluetooth radios…':job.kind==='survey'?'Creating the map, charts, and GIS files…':'Working…';
    if(job.state==='completed'&&job.kind==='preview')message='Local preview ready. No radio connection or RF transmission.';
    if(job.state==='completed'&&job.kind==='capture')message=job.result?.decoded_samples===0?'Capture finished without received audio. Check the receiver and try again.':'Received audio saved. Choose it in step 3 to analyze.';
    if(job.state==='stopped'&&job.kind==='capture')message='Capture canceled early. Audio is saved for diagnosis; finish a timed capture to enable analysis.';
    if(job.state==='completed'&&job.kind==='analyze')message=job.result?.valid_windows?`${job.result.valid_windows} of ${job.result.window_count} valid windows. Open the report to inspect signal quality.`:'No valid tone windows. Open the report for reasons.';
    if(job.result?.stop_verified===true&&job.kind==='transmit')message+=' Radio TX was checked off.';
    if(job.kind==='survey'&&job.result?.mapped_points===0)message='Survey built with no qualified GPS points. Open it to inspect exclusions.';
    c.message.textContent=message;c.when.dateTime=job.created_at;c.when.textContent=timestamp(job.created_at)+(activeStates.has(job.state)?' · '+age(job.created_at)+' elapsed':'');c.log.textContent=job.log||'No diagnostic output.';
    const previous=previousJobStates.get(job.id);if(previous&&previous!==job.state&&['failed','unknown'].includes(job.state))notify(job.title+': '+message,true,true);previousJobStates.set(job.id,job.state);
    const actionSignature=JSON.stringify([job.state,job.files,job.result?.stop_verified]);
    if(c.actionSignature!==actionSignature){
      c.actions.replaceChildren();c.media.replaceChildren();c.fileLinks.replaceChildren();
      if(['running','stopping'].includes(job.state))c.actions.append(button(job.state==='stopping'?'Stopping…':job.kind==='transmit'?'Stop tone':job.kind==='capture'?'Cancel capture':'Stop',async()=>{await api('/api/stop',{id:job.id});await refresh();},job.kind==='transmit'?'danger':'secondary'));
      if(job.state==='unknown')c.actions.append(button('Confirm stopped',async()=>{if(window.confirm('Confirm that this operation has ended and the radio is not transmitting.')){await api('/api/acknowledge',{id:job.id,confirmed:true});await refresh();}},'danger-outline'));
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
  if(tx){$('transmit-banner-text').textContent=tx.state==='unknown'?'Previous transmit test needs attention. Check the radio.':tx.state==='stopping'?'Stopping tone. Waiting for radio TX verification…':'Transmit test active · '+tx.title.replace('Transmit tone · ','');setButton($('stop-tone-banner'),'Stop tone',tx.state!=='running');$('stop-tone-banner').onclick=()=>act($('stop-tone-banner'),async()=>{await api('/api/stop',{id:tx.id});await refresh();});}
}
function processDiscovery(job){
  if(job.state!=='completed'||processedResults.has(job.id)||!job.result)return;
  if(job.kind==='scan'){
    $('scan-panel').hidden=false;$('scan-status').textContent=job.result.candidates.length+' candidates';$('scan-results').replaceChildren();
    for(const candidate of job.result.candidates){const row=node('div',undefined,'scan-row'),details=node('div');details.append(node('strong',candidate.name||'Unnamed radio'),node('small',candidate.address));row.append(details,button('Use this radio',()=>openEditor(null,candidate)));$('scan-results').append(row);}
    if(!job.result.candidates.length)$('scan-results').append(node('p','No candidates found. Enable radio Bluetooth, move it nearby, close other control apps, and try again.','muted'));
    for(const warning of job.result.warnings||[])$('scan-results').append(node('p',warning,'scan-warnings'));
  }else if(job.kind==='services'){
    const count=job.result.filter(service=>service.audio).length;$('audio-support-result').textContent=count?`Found ${count} advertised audio service${count===1?'':'s'}. The test will select a supported audio connection.`:'No supported audio service was advertised. Check Classic Bluetooth pairing and radio firmware.';
  }
  processedResults.add(job.id);
}
$('show-history').onclick=()=>{history=!history;renderJobs();};

function openEditor(profile=null,candidate=null){
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

function toneData(){return {frequency:Number($('tone-frequency').value),duration:Number($('tone-duration').value),level_dbfs:Number($('tone-level').value)};}
function renderAudioButtons(){
  const available=state.capabilities.audio&&!closing;
  const receiver=$('receiver-radio').value,transmitter=$('transmitter-radio').value;
  const receiverJob=receiver&&activeFor(receiver),transmitterJob=transmitter&&activeFor(transmitter);
  setButton($('start-capture'),receiverJob?.kind==='capture'?'Capture in progress':'Start receive capture',!available||!receiver||!!receiverJob);
  setButton($('check-receiver'),'Check audio support',!available||!receiver||!!receiverJob);
  setButton($('preview-tone'),'Create local preview',!available);
  setButton($('review-transmit'),transmitterJob?'Radio busy':'Review RF transmission…',!available||!transmitter||!!transmitterJob);
  setButton($('analyze-audio'),'Analyze signal quality',!available||!$('analysis-audio').value);
}
for(const id of ['receiver-radio','transmitter-radio','analysis-audio'])$(id).onchange=renderAudioButtons;
$('preview-tone').onclick=()=>act($('preview-tone'),()=>launch({kind:'preview',...toneData()},'Preparing a local tone preview. No RF will be sent.'));
$('start-capture').onclick=()=>act($('start-capture'),()=>launch({kind:'capture',profile:$('receiver-radio').value,duration:Number($('capture-duration').value)},'Starting receive-only capture. Wait for Capturing in Activity before sending the tone.'));
$('check-receiver').onclick=()=>act($('check-receiver'),()=>launch({kind:'services',profile:$('receiver-radio').value},'Checking the radio’s advertised Bluetooth audio services.'));
$('analyze-audio').onclick=()=>act($('analyze-audio'),()=>launch({kind:'analyze',audio:$('analysis-audio').value,frequency:Number($('tone-frequency').value)},'Analyzing the received tone. Results will appear in Activity.'));
$('review-transmit').onclick=()=>act($('review-transmit'),async()=>{
  intent=await api('/api/prepare-transmit',{profile:$('transmitter-radio').value,...toneData()});
  $('transmit-review').replaceChildren();for(const [label,value] of [['Radio',intent.radio],['Address',intent.address],['Tone',intent.parameters.frequency+' Hz'],['Duration',intent.parameters.duration+' seconds'],['Digital level',intent.parameters.level_dbfs+' dBFS peak'],['RF settings','Current channel and power on the radio']])$('transmit-review').append(node('dt',label),node('dd',value));
  $('transmit-confirm').checked=false;$('confirm-transmit').disabled=true;$('transmit-error').textContent='';$('transmit-dialog').showModal();
});
$('transmit-confirm').onchange=()=>{$('confirm-transmit').disabled=!$('transmit-confirm').checked;};
$('confirm-transmit').onclick=async()=>{
  const control=$('confirm-transmit');control.disabled=true;
  try{if(!$('transmit-confirm').checked)throw new Error('Confirm the radio settings first.');await api('/api/jobs',{kind:'transmit',intent:intent.intent,confirmed:true});$('transmit-dialog').close();notify('Transmit test started. Use Stop tone to request early cleanup.');await refresh();}
  catch(error){$('transmit-error').textContent=error.message;}finally{control.disabled=!$('transmit-confirm').checked;}
};

async function upload(file,kind,status){
  if(!file)return;if(file.size>128*1024*1024){notify('Choose a file up to 128 MB.',true);return;}
  status.textContent='Importing '+file.name+'…';
  try{const response=await fetch('/api/import?kind='+kind,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-GPS-RSSI-Workspace':token,'X-File-Name':encodeURIComponent(file.name)},body:file});const result=await response.json();if(!response.ok)throw new Error(result.error||'Import failed');status.textContent='Imported '+file.name+'. Your original file is unchanged.';await refresh();}
  catch(error){status.textContent='';notify(error.message,true);}
}
$('import-database').onclick=()=>$('database-file').click();$('database-file').onchange=event=>upload(event.target.files[0],'database',$('database-import-status'));
$('import-audio').onclick=()=>$('audio-file').click();$('audio-file').onchange=event=>upload(event.target.files[0],'audio',$('audio-import-status'));
function openExit(){$('exit-dialog').showModal();}
$('exit-workspace').onclick=openExit;if($('exit-workspace-mobile'))$('exit-workspace-mobile').onclick=openExit;
$('confirm-exit').onclick=()=>act($('confirm-exit'),async()=>{await api('/api/exit',{confirmed:true});closing=true;$('exit-dialog').close();notify('Stopping managed operations and saving data. Allow radio cleanup to finish.');await refresh();});

function render(){
  renderRadios();renderDatasets();renderJobs();renderLive();
  $('workspace-folder').textContent='App folder: '+state.folder;$('audio-unavailable').hidden=state.capabilities.audio;
  renderAudioButtons();
  $('connection-state').textContent=state.closed?'Workspace closed':closing?'Stopping operations…':'Local workspace online';
  if(state.closed){closing=true;clearTimeout(pollTimer);const unsafe=state.jobs.some(j=>activeStates.has(j.state));notify(unsafe?'Workspace closed with a radio warning. Check the radio and its operation details.':'Workspace closed. Managed operations have ended and saved data is retained.',unsafe);}
}
async function refresh(){
  const ticket=++serial,response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw new Error('The local workspace is unavailable.');
  const data=await response.json();if(ticket<applied)return;applied=ticket;state=data;token=data.token;closing=closing||data.closing;render();
}
async function poll(){
  try{await refresh();}catch(error){$('connection-state').textContent='Connection lost · retrying';if(!closing)notify('Cannot reach the local workspace. Existing operations may still be running. Reconnecting…',true);}
  if(!state?.closed)pollTimer=setTimeout(poll,2000);
}
poll();
