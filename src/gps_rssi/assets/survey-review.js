'use strict';
// Row indices identify observations, even when sample IDs repeat across sessions.
const SurveyReview={create(rows,{fields,filename,onChange}){
  const selected=new Set(),removed=new Set(),history=[];
  const el=id=>document.getElementById(id);
  let visible=new Set();
  function update(){
    el('review-status').textContent=`${selected.size} selected · ${removed.size} removed · ${rows.length-removed.size} retained for export`;
    el('review-remove').disabled=!selected.size;el('review-undo').disabled=!history.length;
    for(const tr of document.querySelectorAll('tr[data-row]')){
      const key=tr.getAttribute('data-row');
      let box=tr.reviewCheckbox;
      if(!box){
        const cell=document.createElement('td');box=document.createElement('input');box.type='checkbox';
        box.setAttribute('aria-label',`Select observation ${Number(key)+1}`);
        box.onchange=()=>toggle(key);cell.appendChild(box);
        if(tr.firstChild&&tr.firstChild.textContent==='')tr.firstChild.appendChild(box);
        else tr.insertBefore(cell,tr.firstChild);
        tr.reviewCheckbox=box;
      }
      box.checked=selected.has(key);
    }
  }
  function changed(){update();onChange();}
  function toggle(key){key=String(key);if(removed.has(key)||!visible.has(key))return;
    if(selected.has(key))selected.delete(key);else selected.add(key);changed();}
  function csv(){
    const quote=value=>{
      if(value===null||value===undefined)return '';
      let text=typeof value==='object'?JSON.stringify(value):typeof value==='boolean'?(value?'True':'False'):String(value);
      // Prevent stored text from becoming a formula when opened in a spreadsheet.
      if(typeof value==='string'&&/^[\s]*[=+\-@]/.test(text))text="'"+text;
      return '"'+text.replace(/"/g,'""')+'"';
    };
    return [fields.map(quote).join(','),...rows.flatMap((row,i)=>removed.has(String(i))?[]:[fields.map(f=>quote(row[f])).join(',')])].join('\r\n')+'\r\n';
  }
  el('review-select').onclick=()=>{for(const key of visible)selected.add(key);changed();};
  el('review-clear').onclick=()=>{selected.clear();changed();};
  el('review-remove').onclick=()=>{
    if(!selected.size)return;
    const batch=[...selected];history.push(batch);for(const key of batch)removed.add(key);selected.clear();changed();
  };
  el('review-undo').onclick=()=>{const batch=history.pop();if(!batch)return;for(const key of batch)removed.delete(key);changed();};
  el('review-export').onclick=()=>{
    const url=URL.createObjectURL(new Blob([csv()],{type:'text/csv;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  return {removed,selected,csv,toggle,mapClick(key){if(el('review-mode').checked)toggle(key);},
    setVisible(keys){visible=new Set(keys);for(const key of selected)if(!visible.has(key))selected.delete(key);update();}};
}};
