// ==UserScript==
// @name         DiscogsBot Stage 3 — Votable Release Link Submitter
// @namespace    https://musicbrainz.org/
// @version      0.1.3
// @description  Submit reviewed DiscogsBot v5 candidates as votable MusicBrainz release URL edits.
// @match        https://musicbrainz.org/*
// @match        https://test.musicbrainz.org/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      musicbrainz.org
// ==/UserScript==

(() => {
  'use strict';
  const VERSION = '0.1.3', EXPECTED_BOT_USER = location.hostname === 'test.musicbrainz.org' ? 'TestBot' : 'WindowsLover_DiscogsBot', LINK_TYPE_ID = 76, EDIT_TYPE = 90, MAX_BATCH = 150, GAP = 1100;
  const BLACKLIST_URL = 'https://musicbrainz.org/doc/Bots/Blacklist/Discogs_Links';
  const DAILY_LIMIT = 1000, OPEN_EDIT_PROJECT_LIMIT = 2000, OPEN_EDIT_HARD_LIMIT = 2500;
  const KEY = 'discogsbot-stage3:' + location.host + ':state-v1';
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let state = readState(), queue = null, checked = [], results = [], busy = false, complete = false, last = 0, panel;
  let currentBlacklist = null, openEditCount = null, blacklistCheckedAt = null;

  function readState() {
    try { const s = JSON.parse(localStorage.getItem(KEY) || '{}'); return {submitted:s.submitted || {}, uncertain:s.uncertain || {}}; }
    catch (_) { return {submitted:{}, uncertain:{}}; }
  }
  function saveState() { localStorage.setItem(KEY, JSON.stringify(state)); }
  async function pace() { const d = Math.max(0, GAP - (Date.now() - last)); if (d) await sleep(d); last = Date.now(); }
  async function mbFetch(path, init) {
    await pace();
    return fetch(location.origin + path, Object.assign({credentials:'include', headers:{Accept:'application/json'}}, init || {}));
  }
  function gmGetText(url) {
    return new Promise((resolve, reject) => {
      const request = typeof GM_xmlhttpRequest === 'function'
        ? GM_xmlhttpRequest
        : (typeof GM !== 'undefined' && GM && typeof GM.xmlHttpRequest === 'function' ? GM.xmlHttpRequest : null);
      if (!request) { reject(new Error('Tampermonkey GM_xmlhttpRequest is unavailable.')); return; }
      request({
        method: 'GET', url, headers: {Accept:'text/html'}, timeout: 30000,
        onload: response => response.status === 200 ? resolve(String(response.responseText || '')) : reject(new Error('Blacklist HTTP ' + response.status)),
        onerror: () => reject(new Error('Network error while downloading the official Discogs blacklist.')),
        ontimeout: () => reject(new Error('Timed out while downloading the official Discogs blacklist.'))
      });
    });
  }
  async function fetchDiscogsBlacklist() {
    const html = await gmGetText(BLACKLIST_URL);
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const ids = new Set();
    for (const a of doc.querySelectorAll('a[href]')) {
      let url;
      try { url = new URL(a.getAttribute('href'), BLACKLIST_URL); } catch (_) { continue; }
      const match = url.pathname.match(/^\/release\/([0-9a-f-]{36})(?:\/|$)/i);
      if (match && mbidOK(match[1])) ids.add(match[1].toLowerCase());
    }
    if (!ids.size) throw new Error('Could not extract MusicBrainz Release MBIDs from the official Discogs blacklist. Failing closed: no edits can be submitted.');
    return ids;
  }
  async function verifyRunLoginAndOpenEdits() {
    if (!isBotAccount()) throw new Error('Wrong account. Required: ' + EXPECTED_BOT_USER);
    const path = '/user/' + encodeURIComponent(EXPECTED_BOT_USER) + '/edits/open';
    const response = await mbFetch(path, {headers:{Accept:'text/html'}});
    const html = await response.text();
    if (!response.ok || (response.redirected && /\/login(?:[?#]|$)/i.test(response.url))) throw new Error('MusicBrainz login/session check failed. Sign in as ' + EXPECTED_BOT_USER + ' and start this run again.');
    const found = html.match(/Found\s+(at least\s+)?([0-9,]+)\s+edits?/i);
    if (found && found[1]) throw new Error('MusicBrainz reports only an approximate open-edit count ('+found[2]+'). No submission is allowed until an exact count is available.');
    if (found) return Number(found[2].replace(/,/g, ''));
    if (/No edits found|There are no open edits/i.test(html)) return 0;
    throw new Error('Could not read the total open-edit count from the bot profile. No submission is allowed until the count can be verified.');
  }
  function mbidOK(s) { return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(s || '')); }
  function discogsID(value) {
    try {
      const u = new URL(value);
      if (u.protocol !== 'https:' || !['discogs.com','www.discogs.com'].includes(u.hostname.toLowerCase())) return '';
      const m = u.pathname.match(/^\/release\/(\d+)(?:-|\/|$)/i);
      return m ? m[1] : '';
    } catch (_) { return ''; }
  }
  function validate(data) {
    if (!data || data.schema_version !== 1 || !Array.isArray(data.items) || !data.items.length || data.items.length > MAX_BATCH) throw Error('Invalid queue schema or size (maximum 150).');
    if (data.source_engine !== 'DiscogsBot Stage 2 v5') throw Error('Queue must originate from the official DiscogsBot Stage 2 v5 report.');
    if (data.target_server !== location.hostname) throw Error('Queue target is ' + (data.target_server || 'unspecified') + ', but this page is ' + location.hostname + '. Refusing cross-server submission.');
    const policy = data.submission_policy || {};
    if (Number(policy.link_type_id) !== LINK_TYPE_ID || Number(policy.edit_type) !== EDIT_TYPE || policy.make_votable !== true) throw Error('Queue submission policy does not match the required votable Discogs Release relationship.');
    const mbids = new Set(), ids = new Set();
    data.items.forEach((x,i) => {
      const id = discogsID(x.discogs_url), mbid = String(x.mbid || '').toLowerCase();
      if (x.status !== 'AUTO_CANDIDATE' || !mbidOK(mbid) || !id || id !== String(x.discogs_release_id)) throw Error('Invalid item ' + (i+1) + ': status, MBID or Discogs URL.');
      if (mbids.has(mbid) || ids.has(id)) throw Error('Duplicate MBID or Discogs Release ID in queue.');
      mbids.add(mbid); ids.add(id); x.mbid = mbid;
    });
    return data;
  }
  function userHint() {
    const link = Array.from(document.querySelectorAll('a[href]')).find(a => /^\/user\/[^/]+\/?$/i.test(a.getAttribute('href') || ''));
    if (!link) return 'not detected — sign in as the bot account';
    const match = (link.getAttribute('href') || '').match(/^\/user\/([^/]+)\/?$/i);
    return match ? decodeURIComponent(match[1]) : link.textContent.trim();
  }
  function normURL(s) { try { const u=new URL(s); return u.hostname.toLowerCase().replace(/^www\./,'')+u.pathname.replace(/\/+$/,''); } catch (_) { return String(s||'').toLowerCase(); } }
  function detectedUsername() {
    const link = Array.from(document.querySelectorAll('a[href]')).find(a => /^\/user\/[^/]+\/?$/i.test(a.getAttribute('href') || ''));
    if (!link) return '';
    const match = (link.getAttribute('href') || '').match(/^\/user\/([^/]+)\/?$/i);
    try { return match ? decodeURIComponent(match[1]) : ''; } catch (_) { return match ? match[1] : ''; }
  }
  function isBotAccount() { return detectedUsername() === EXPECTED_BOT_USER; }
  async function checkOne(item) {
    if (!currentBlacklist) throw new Error('The official Discogs blacklist has not been loaded. Run preflight again.');
    if (currentBlacklist.has(item.mbid)) return {item,status:'BLOCKED_BLACKLIST',detail:'MusicBrainz Release MBID is on the current official Discogs blacklist.'};
    if (state.submitted[item.mbid]) return {item,status:'PREVIOUSLY_SUBMITTED',detail:'Recorded in local ledger.'};
    if (state.uncertain[item.mbid]) return {item,status:'UNCERTAIN_DO_NOT_RETRY',detail:'Previous POST may have succeeded. Inspect bot open edits.'};
    const r = await mbFetch('/ws/js/entity/' + item.mbid + '?inc=rels');
    const raw = await r.text();
    if (!r.ok) throw Error('Relationship check HTTP ' + r.status + ' for ' + item.mbid);
    let data; try { data=JSON.parse(raw); } catch (_) { throw Error('Non-JSON relationship response for ' + item.mbid); }
    const rels = (data.relationships || []).filter(x => x.target_type === 'url');
    const discogs = rels.filter(x => Number(x.linkTypeID) === LINK_TYPE_ID || /^https?:\/\/(?:www\.)?discogs\.com\/release\/\d+/i.test((x.target||{}).name||''));
    if (!discogs.length) return {item,status:'READY',detail:'No Discogs release URL is linked.'};
    const exact = discogs.some(x => {
      const url=(x.target||{}).name||'', m=url.match(/\/release\/(\d+)/i);
      return (m && m[1] === item.discogs_release_id) || normURL(url) === normURL(item.discogs_url);
    });
    return {item,status:exact?'ALREADY_LINKED':'OTHER_DISCOGS_RELEASE_LINK',detail:exact?'Exact Discogs release URL already exists.':'Another Discogs release URL already exists.'};
  }
  function status(message,kind) { const e=panel.querySelector('#db-status'); e.textContent=message; e.dataset.kind=kind||'info'; }
  function progress(n,total,label) { panel.querySelector('#db-progress').value=total?Math.round(n*100/total):0; panel.querySelector('#db-progress-text').textContent=label; }
  function render() {
    if (!panel) return;
    const rows = checked.length ? checked : (queue ? queue.items.map(item=>({item,status:'NOT_CHECKED'})) : []);
    panel.querySelector('#db-rows').innerHTML=rows.map(row=>{
      const result=results.find(x=>x.item.mbid===row.item.mbid);
      return '<tr><td>'+esc(row.item.mb_artist)+' — '+esc(row.item.mb_title)+'</td><td>'+esc(row.item.discogs_release_id)+'</td><td>'+esc(result?result.status:row.status)+'</td></tr>';
    }).join('');
    const ready=checked.filter(x=>x.status==='READY'&&!results.some(y=>y.item.mbid===x.item.mbid)).length;
    panel.querySelector('#db-count').textContent=queue?queue.items.length+' records loaded':'No queue loaded';
    panel.querySelector('#db-open-count').textContent=openEditCount===null?'Open edits: not checked':('Open edits: '+openEditCount+'; projected after READY items: '+(openEditCount+checked.filter(x=>x.status==='READY').length));
    panel.querySelector('#db-blacklist-count').textContent=currentBlacklist?('Official Discogs blacklist checked '+new Date(blacklistCheckedAt).toLocaleTimeString()+'; '+currentBlacklist.size+' release IDs loaded'):'Official Discogs blacklist: not checked';
    panel.querySelector('#db-check').disabled=!queue||busy;
    panel.querySelector('#db-account').disabled=!isBotAccount();
    if (!isBotAccount()) panel.querySelector('#db-account').checked=false;
    panel.querySelector('#db-account-label').textContent=isBotAccount()?'Verified bot account: '+EXPECTED_BOT_USER:'BLOCKED — signed in as '+(detectedUsername()||'unknown')+'. Required: '+EXPECTED_BOT_USER;
    panel.querySelector('#db-submit').disabled=!complete||!isBotAccount()||!panel.querySelector('#db-account').checked||busy||!ready;
    panel.querySelector('#db-submit').textContent='Submit '+ready+' READY as votable';
    panel.querySelector('#db-download').disabled=!checked.length&&!results.length;
  }
  async function preflight() {
    if (!queue||busy) return;
    if (!isBotAccount()) { status('BLOCKED: sign in as '+EXPECTED_BOT_USER+'.','error'); return; }
    busy=true; complete=false; checked=[]; results=[]; currentBlacklist=null; openEditCount=null; blacklistCheckedAt=null; render();
    status('Verifying the current bot login and open-edit count…','info');
    try {
      openEditCount = await verifyRunLoginAndOpenEdits();
      status('Downloading the current official Discogs blacklist…','info');
      currentBlacklist = await fetchDiscogsBlacklist();
      blacklistCheckedAt = new Date().toISOString();
      for(let i=0;i<queue.items.length;i++){progress(i,queue.items.length,'Checking '+(i+1)+'/'+queue.items.length);checked.push(await checkOne(queue.items[i]));render();}
      complete=true; const n=checked.filter(x=>x.status==='READY').length;
      const blocked=checked.filter(x=>x.status==='BLOCKED_BLACKLIST').length;
      progress(queue.items.length,queue.items.length,'Preflight complete: '+n+' READY; '+(checked.length-n)+' skipped.');
      status('Preflight complete. Login verified as '+EXPECTED_BOT_USER+'; open edits: '+openEditCount+'; fresh blacklist: '+currentBlacklist.size+' release IDs ('+blocked+' blocked). Enter other daily edits before submission.','success');
    } catch(e) { status('Preflight stopped: '+e.message,'error'); }
    finally { busy=false;render(); }
  }
  function note(item) {
    return [
      'Added the corresponding Discogs release page after Discogs Link Bot v5 conservative metadata verification.',
      'v5 checks artist, title, barcode and all available label/catalog-number data.',
      'v5 result: '+(item.reason||'AUTO_CANDIDATE')+'.',
      item.normalization_actions?'Safe normalizations: '+item.normalization_actions+'.':'',
      'Bot source: https://github.com/Echelon666/discogs-link-bot'
    ].filter(Boolean).join('\n');
  }
  async function submitOne(item) {
    if(state.uncertain[item.mbid]) throw Error('UNCERTAIN: unresolved previous POST for '+item.mbid);
    const fresh=await checkOne(item);
    if(fresh.status!=='READY') return {item,status:'SKIPPED_'+fresh.status,detail:fresh.detail};
    state.uncertain[item.mbid]={at:new Date().toISOString(),batch:queue.batch_id||''};saveState();
    const payload={edits:[{edit_type:EDIT_TYPE,linkTypeID:LINK_TYPE_ID,attributes:[],entities:[
      {entityType:'release',gid:item.mbid},{entityType:'url',name:item.discogs_url}
    ]}],editNote:note(item),makeVotable:1};
    let r;
    try { r=await mbFetch('/ws/js/edit/create',{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify(payload)}); }
    catch(e){throw Error('UNCERTAIN: network error, do not retry: '+e.message);}
    const raw=await r.text();let json=null;try{json=JSON.parse(raw);}catch(_){}
    if(r.status===429||r.status===503||r.status>=500) throw Error('UNCERTAIN: HTTP '+r.status+', inspect open edits before retrying.');
    if(!r.ok||(json&&json.error)){delete state.uncertain[item.mbid];saveState();throw Error('REJECTED: HTTP '+r.status+' '+(json?JSON.stringify(json.error):raw.slice(0,220)));}
    if(!json||typeof json!=='object') throw Error('UNCERTAIN: non-JSON success response; inspect open edits before retrying.');
    state.submitted[item.mbid]={at:new Date().toISOString(),batch:queue.batch_id||'',discogs_release_id:item.discogs_release_id};
    delete state.uncertain[item.mbid];saveState();
    return {item,status:'SUBMITTED_VOTABLE',detail:'MusicBrainz accepted the edit request.'};
  }
  function countSubmittedToday() {
    const today = new Date().toISOString().slice(0, 10);
    return Object.values(state.submitted).filter(entry => {
      const stamp = entry && (entry.at || entry.submitted_at);
      return stamp && new Date(stamp).toISOString().slice(0, 10) === today;
    }).length;
  }
  async function submit() {
    if (!isBotAccount()) { status('BLOCKED: signed in as '+(detectedUsername()||'unknown')+'. Only '+EXPECTED_BOT_USER+' may submit bot edits.','error'); return; }
    if(!queue||busy||!complete)return;
    if(queue.items.some(x=>state.uncertain[x.mbid])){status('Uncertain previous POST blocks this queue. Inspect bot open edits.','error');return;}
    const ready=checked.filter(x=>x.status==='READY'&&!results.some(y=>y.item.mbid===x.item.mbid));
    if(!ready.length)return;
    if (openEditCount === null || !currentBlacklist || !blacklistCheckedAt) { status('BLOCKED: login, open-edit count and fresh blacklist must all be verified in this run.','error'); return; }
    const otherDailyRaw = panel.querySelector('#db-other-daily').value.trim();
    if (otherDailyRaw === '' || !/^\d+$/.test(otherDailyRaw)) { status('Enter other bot edits submitted today outside this browser (enter 0 when none).','error'); return; }
    const otherDaily = Number(otherDailyRaw), localDaily = countSubmittedToday();
    const projectedOpen = openEditCount + ready.length;
    const projectedDaily = otherDaily + localDaily + ready.length;
    if (projectedOpen > OPEN_EDIT_PROJECT_LIMIT) { status('BLOCKED: current open edits ('+openEditCount+') plus this batch ('+ready.length+') would exceed the project ceiling of '+OPEN_EDIT_PROJECT_LIMIT+'. MusicBrainz hard limit is '+OPEN_EDIT_HARD_LIMIT+'.','error'); return; }
    if (projectedOpen > OPEN_EDIT_HARD_LIMIT) { status('BLOCKED: this batch would exceed the MusicBrainz hard open-edit limit of '+OPEN_EDIT_HARD_LIMIT+'.','error'); return; }
    if (projectedDaily > DAILY_LIMIT) { status('BLOCKED: other edits today ('+otherDaily+') + local ledger ('+localDaily+') + this batch ('+ready.length+') exceeds the daily limit of '+DAILY_LIMIT+'.','error'); return; }
    const prod=location.hostname==='musicbrainz.org';
    if(prod){const phrase='SUBMIT '+ready.length;if(prompt('PRODUCTION: '+ready.length+' public VOTABLE edits. Type '+phrase)!==phrase)return;}
    else if(!confirm('TEST SERVER: submit '+ready.length+' votable edits?'))return;
    if(!confirm('Final confirmation: submit '+ready.length+' Discogs release links as VOTABLE edits? The bot will not vote.'))return;
    busy=true;render();status('Submitting one edit at a time. Do not close this tab. Ambiguous responses stop the batch.','info');
    try{
      for(let i=0;i<ready.length;i++){
        progress(i,ready.length,'Submitting '+(i+1)+'/'+ready.length);
        try{results.push(await submitOne(ready[i].item));}
        catch(e){results.push({item:ready[i].item,status:/^UNCERTAIN:/.test(e.message)?'UNKNOWN_DO_NOT_RETRY':'REJECTED',detail:e.message});render();status('Stopped: '+e.message,'error');break;}
        render();
      }
      const ok=results.filter(x=>x.status==='SUBMITTED_VOTABLE').length;
      const skipped=results.filter(x=>x.status.startsWith('SKIPPED_')).length;
      const bad=results.some(x=>x.status==='REJECTED'||x.status==='UNKNOWN_DO_NOT_RETRY');
      progress(results.length,ready.length,'Submitted '+ok+'; skipped '+skipped+'.');
      if(!bad&&ok+skipped===ready.length)status('Requests completed. Verify the bot account open-edit list and confirm the edits are votable.','success');
    }finally{busy=false;render();}
  }
  function download() {
    const all=new Map();
    checked.forEach(x=>all.set(x.item.mbid,{item:x.item,check:x.status,detail:x.detail||''}));
    results.forEach(x=>{const r=all.get(x.item.mbid)||{item:x.item};r.submit=x.status;r.detail=x.detail||r.detail||'';all.set(x.item.mbid,r);});
    const rows=[['mbid','discogs_release_id','discogs_url','check_status','submission_status','detail']];
    all.forEach(x=>rows.push([x.item.mbid,x.item.discogs_release_id,x.item.discogs_url,x.check||'',x.submit||'',x.detail||'']));
    const blob=new Blob([rows.map(row=>row.map(v=>String(v||'').replace(/[\t\r\n]+/g,' ')).join('\t')).join('\r\n')],{type:'text/tab-separated-values;charset=utf-8'});
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='discogsbot_stage3_result.tsv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function build() {
    panel=document.createElement('section');panel.id='discogsbot-stage3';
    panel.innerHTML='<div class="head"><b>DiscogsBot Stage 3 v'+VERSION+'</b><button id="db-close">×</button></div>'+
      '<div class="small">'+esc(location.host)+' · '+(location.hostname==='musicbrainz.org'?'PRODUCTION':'TEST')+' · Session: '+esc(userHint())+'</div>'+
      '<label class="confirm"><input type="checkbox" id="db-account"> <span id="db-account-label"></span></label>'+
      '<label class="file">Load submission_batch.json <input type="file" id="db-file" accept=".json,application/json"></label>'+
      '<div id="db-count">No queue loaded</div><div id="db-summary">Only AUTO_CANDIDATE rows from Stage 2 v5; maximum 150; exact server match required.</div>'+'<div class="db-policy"><div id="db-open-count">Open edits: not checked</div><div id="db-blacklist-count">Official Discogs blacklist: not checked</div><label>Other bot edits already submitted today outside this browser (enter 0 if none): <input type="number" min="0" step="1" id="db-other-daily" value="0"></label></div>'+
      '<div class="actions"><button id="db-check" disabled>1. Check existing links</button><button id="db-submit" disabled>Submit 0 READY as votable</button></div>'+
      '<progress id="db-progress" max="100" value="0"></progress><div id="db-progress-text">Idle</div>'+
      '<div id="db-status" data-kind="info">No edit is sent before preflight and explicit confirmation. The bot never votes or auto-approves.</div>'+
      '<div class="table"><table><thead><tr><th>Release</th><th>Discogs ID</th><th>Status</th></tr></thead><tbody id="db-rows"></tbody></table></div>'+
      '<button id="db-download" disabled>Download TSV report</button><div class="small">Votable edits · login checked every run · fresh official blacklist · open-edit/daily caps · fresh pre-POST check · 1.1 s spacing · no automatic POST retries · local ledger</div>';
    const style=document.createElement('style');
    style.textContent='#discogsbot-stage3 .db-policy{display:grid;gap:5px;padding:7px;background:#fff8e8;margin:7px 0;font-size:11px}#discogsbot-stage3 .db-policy input{width:76px;margin-left:6px}#discogsbot-stage3{position:fixed;z-index:2147483647;right:16px;bottom:16px;width:min(650px,calc(100vw - 32px));max-height:80vh;overflow:auto;background:#fff;color:#202124;border:1px solid #777;border-radius:9px;box-shadow:0 5px 24px #0005;padding:12px;font:13px/1.4 Arial,sans-serif}#discogsbot-stage3 *{box-sizing:border-box}#discogsbot-stage3 .head,#discogsbot-stage3 .actions{display:flex;justify-content:space-between;gap:8px;align-items:center}#discogsbot-stage3 button{cursor:pointer;padding:7px 10px;border:1px solid #888;border-radius:5px;background:#f4f4f4;color:#111}#discogsbot-stage3 button:disabled{opacity:.45;cursor:not-allowed}#discogsbot-stage3 .small{font-size:11px;color:#555;margin:6px 0}#discogsbot-stage3 .confirm{display:block;padding:8px;background:#fff7e6;margin:8px 0}#discogsbot-stage3 .file{display:block;font-weight:bold;margin:8px 0}#discogsbot-stage3 input[type=file]{display:block;max-width:100%;margin-top:4px}#discogsbot-stage3 #db-summary{padding:7px;background:#f4f4f4;margin:7px 0}#discogsbot-stage3 progress{width:100%}#discogsbot-stage3 #db-status{padding:8px;background:#f6f6f6;border-left:4px solid #777;margin:7px 0;overflow-wrap:anywhere}#discogsbot-stage3 #db-status[data-kind=success]{border-color:#16803c}#discogsbot-stage3 #db-status[data-kind=error]{border-color:#b42318}#discogsbot-stage3 .table{max-height:190px;overflow:auto;border:1px solid #ddd}#discogsbot-stage3 table{border-collapse:collapse;width:100%;font-size:11px}#discogsbot-stage3 th,#discogsbot-stage3 td{text-align:left;padding:4px 6px;border-bottom:1px solid #ddd}#discogsbot-stage3 th{position:sticky;top:0;background:#eee}';
    document.documentElement.appendChild(style);document.body.appendChild(panel);
    panel.querySelector('#db-close').onclick=()=>{panel.remove();style.remove();panel=null;};
    panel.querySelector('#db-account').onchange=render;panel.querySelector('#db-check').onclick=preflight;panel.querySelector('#db-submit').onclick=submit;panel.querySelector('#db-download').onclick=download;
    panel.querySelector('#db-file').onchange=async e=>{
      try{queue=validate(JSON.parse(await e.target.files[0].text()));checked=[];results=[];complete=false;currentBlacklist=null;openEditCount=null;blacklistCheckedAt=null;panel.querySelector('#db-account').checked=false;panel.querySelector('#db-summary').textContent=(queue.batch_id||'batch')+' · '+queue.items.length+' items · '+queue.source_engine+' · target: '+queue.target_server+' · AUTO_CANDIDATE only';status('Queue loaded. Run preflight to verify login, open edits, current blacklist and existing relationships.','success');}
      catch(err){queue=null;checked=[];results=[];complete=false;currentBlacklist=null;openEditCount=null;blacklistCheckedAt=null;status('Queue rejected: '+err.message,'error');}render();
    };
    render();
  }
  if(document.body&&!document.getElementById('discogsbot-stage3'))build();
})();
