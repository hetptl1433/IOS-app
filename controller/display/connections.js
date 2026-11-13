'use strict';
// Setup uses the same authenticated controller actions as the phone. No data
// entered here is treated as a successful hardware connection until probed.
(() => {
  const $ = (id) => document.getElementById(id);
  const dialog = $('connection-dialog');
  let state = null, received = 0, draft = null, dirty = false, carDirty = false, busy = false;
  let teslaReturnURL = null;
  const label = (mode) => ({virtual:'Virtual Car',tesla:'Tesla Car',smart_plug:'Smart Plug'})[mode] || 'Virtual Car';
  const message = (text) => { $('connection-message').textContent = text; $('tesla-live-message').textContent = text; };
  const isFresh = () => state && performance.now() - received < 15000;
  const carSource = () => draft && (draft.mode === 'virtual' || (draft.mode === 'smart_plug' && draft.smartPlug.vehicleSource === 'virtual'));
  const teslaSource = () => draft && (draft.mode === 'tesla' || (draft.mode === 'smart_plug' && draft.smartPlug.vehicleSource === 'tesla'));
  function load() {
    if (!state) return;
    draft = structuredClone(state); dirty = false; carDirty = false;
    $('connection-plug-address').value = draft.smartPlug.address || '';
    $('connection-plug-source').value = draft.smartPlug.vehicleSource;
    $('connection-virtual-model').value = draft.virtual.model;
    $('connection-virtual-battery').value = draft.virtual.batteryPercent;
    $('connection-virtual-plugged').checked = draft.virtual.pluggedIn;
    render();
  }
  function show(mode) {
    if (!draft || !dirty) load();
    if (!draft) { message('Connect to the controller first.'); return; }
    draft.mode = mode; dirty = true; dialog.hidden = false; render();
    $('connection-close').focus();
  }
  function render() {
    if (!state) return;
    const model = state.virtual.model;
    const modelName = {cybertruck:'Cybertruck',model_x:'Model X',model_3:'Model 3'}[model] || 'Virtual Car';
    $('vehicle-image').hidden = model !== 'cybertruck';
    $('vehicle-generic').hidden = model === 'cybertruck';
    document.querySelector('.lightbar').hidden = model !== 'cybertruck';
    $('vehicle-generic-name').textContent = modelName;
    $('vehicle-generic').setAttribute('aria-label','Virtual '+modelName);
    $('connection-current').textContent = 'Current charging: '+label(state.activeMode || 'virtual');
    $('connection-summary').textContent = 'Saved setup: '+label(state.mode)+' · '+(state.smartPlug.verified ? 'Shelly verified' : 'Shelly not connected')+' · '+(state.tesla.authorized ? 'Tesla authorized' : 'Tesla not authorized');
    const activeTesla = state.activeMode === 'tesla', live = state.tesla.chargeState;
    $('view-charge').classList.toggle('is-tesla', activeTesla);
    $('tesla-charge').hidden = !activeTesla;
    $('view-schedule').classList.toggle('is-tesla', activeTesla);
    let note = $('tesla-schedule-note');
    if (!note) { note = document.createElement('p'); note.id = 'tesla-schedule-note'; note.className = 'tile tesla-schedule-note'; note.textContent = 'Tesla charging is manual. The virtual nightly test is paused. Use Charge to start or stop your Tesla.'; $('view-schedule').prepend(note); }
    note.hidden = !activeTesla;
    $('tesla-live-name').textContent = state.tesla.vehicleName || 'Tesla';
    $('tesla-live-status').textContent = live ? `${live.stale ? 'Older reading' : 'Vehicle reading'} · ${live.state} · ${new Date(live.updatedAt).toLocaleTimeString()}` : 'Refresh to read your car.';
    $('tesla-live-battery').textContent = live && Number.isFinite(live.batteryPercent) ? `${live.batteryPercent}%` : '—';
    $('tesla-live-power').textContent = live && !live.stale && Number.isFinite(live.powerKW) ? `${live.powerKW} kW · ${live.pluggedIn ? 'Plugged in' : 'Unplugged'}` : 'Current power unavailable · refresh the car';
    for (const button of document.querySelectorAll('[data-tesla-command]')) button.disabled = busy || !isFresh() || !activeTesla || !state.tesla.commandsReady;
    for (const button of document.querySelectorAll('[data-tesla-refresh]')) button.disabled = busy || !isFresh() || !state.tesla.authorized;
    if (!draft || dialog.hidden) return;
    $('connection-title').textContent = label(draft.mode);
    $('connection-active').textContent = 'Current charging: '+label(state.activeMode || 'virtual')+(activeTesla ? ' · manual controls' : ' · virtual test');
    for (const button of $('connection-modes').querySelectorAll('button')) button.classList.toggle('active', button.dataset.mode === draft.mode);
    $('connection-plug-section').hidden = draft.mode !== 'smart_plug';
    $('connection-virtual-section').hidden = !carSource();
    $('connection-tesla-section').hidden = !teslaSource();
    const plug = state.smartPlug, tesla = state.tesla;
    $('connection-plug-status').textContent = plug.verified
      ? `Connected · ${plug.switchOn === true ? 'On' : plug.switchOn === false ? 'Off' : 'Relay unknown'} · ${Number.isFinite(plug.powerW) ? plug.powerW.toFixed(1)+' W' : 'Meter unavailable'}`
      : plug.error || (plug.address ? 'Saved address · connect to verify' : 'Enter the plug’s local IP address');
    $('connection-virtual-live').textContent = `Controller now: ${state.virtual.batteryPercent}% · ${state.virtual.pluggedIn ? 'plugged in' : 'unplugged'}`;
    $('connection-tesla-status').textContent = !tesla.configured ? 'Tesla setup is needed privately on the Pi.' : !tesla.authorized ? 'Authorize access on Tesla’s website. SPARC never asks for your Tesla password.' : tesla.vehicleName ? 'Authorized · '+tesla.vehicleName : 'Account authorized · refresh and select your Tesla';
    if (tesla.error) $('connection-tesla-status').textContent += ' · '+tesla.error;
    const charge = tesla.chargeState;
    $('connection-tesla-charge').textContent = charge ? `${charge.batteryPercent}% · ${charge.pluggedIn ? 'plugged in' : 'unplugged'} · ${charge.powerKW} kW · ${charge.stale ? 'Older reading' : 'Read'} ${new Date(charge.updatedAt).toLocaleTimeString()}` : 'Vehicle charging data has not been read yet.';
    $('connection-tesla-key-hint').textContent = tesla.commandsReady ? 'Vehicle key and signed charging connection are ready.' : 'Real charging commands also require pairing the SPARC vehicle key in Tesla and a ready signed connection.';
    const list = $('connection-tesla-vehicle');
    const chosen = list.value;
    list.replaceChildren(new Option('Choose a vehicle',''));
    for (const vehicle of tesla.vehicles || []) list.add(new Option(vehicle.displayName,vehicle.id));
    list.value = chosen || tesla.vehicleId || '';
    const locked = busy || !isFresh();
    for (const button of dialog.querySelectorAll('button:not(#connection-close)')) button.disabled = locked;
    $('connection-save').disabled = locked || !dirty;
    $('connection-plug-on').disabled = locked || !plug.verified;
    $('connection-plug-off').disabled = locked || !plug.verified;
    $('connection-tesla-authorize').disabled = locked || !tesla.configured;
    $('connection-tesla-complete').disabled = locked || !teslaReturnURL || tesla.authorized;
    $('connection-tesla-refresh').disabled = locked || !tesla.authorized;
    $('connection-tesla-select').disabled = locked || !tesla.authorized;
    $('connection-tesla-disconnect').disabled = locked || !tesla.authorized;
    $('connection-tesla-key').disabled = locked || !tesla.keyPairingURL;
    $('connection-tesla-activate').disabled = locked || !tesla.commandsReady || !charge || charge.stale || activeTesla;
    $('connection-virtual-activate').disabled = locked || !activeTesla;
    for (const button of dialog.querySelectorAll('[data-tesla-command]')) button.disabled = locked || !activeTesla || !tesla.commandsReady;
    for (const field of [$('connection-virtual-battery'),$('connection-virtual-plugged')]) field.disabled = locked || activeTesla;
    if (!isFresh()) message('Reconnect to the Pi before applying changes.');
  }
  async function send(path, body) {
    if (busy || !isFresh()) return null;
    busy = true; render();
    try {
      const response = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const result = await response.json();
      if (!response.ok || result.ok !== true) throw new Error(result.error || 'The controller could not complete this action.');
      document.dispatchEvent(new CustomEvent('sparc-connections-updated',{detail:result}));
      return result;
    } catch (error) { message(error.message || 'Connection action could not be confirmed. Refresh before retrying.'); return null; }
    finally { busy = false; render(); }
  }
  async function save() {
    if (!draft) return false;
    const body = {mode:draft.mode,expectedRevision:state.revision};
    if (carSource()) {
      body.virtual = {model:$('connection-virtual-model').value};
      if (carDirty) {
        const battery = Number($('connection-virtual-battery').value);
        if (!Number.isFinite(battery) || battery < 0 || battery > 100) { message('Choose a battery percentage from 0 to 100.'); return false; }
        body.virtual.batteryPercent = battery; body.virtual.pluggedIn = $('connection-virtual-plugged').checked;
      }
    }
    if (draft.mode === 'smart_plug') body.smartPlug = {address:$('connection-plug-address').value.trim(),vehicleSource:$('connection-plug-source').value};
    const result = await send('/api/connections',body);
    if (result) { load(); message('Setup saved on the Pi. The active charging connection is unchanged.'); }
    return !!result;
  }
  document.addEventListener('sparc-state',(event) => {
    if (!event.detail.connections) return;
    const previous = state?.revision;
    state = event.detail.connections; received = performance.now();
    if (!dirty) load();
    else if (previous !== undefined && previous !== state.revision) message('Setup changed on the controller. Your edits are kept; reload or save to apply yours.');
    render();
  });
  for (const button of document.querySelectorAll('[data-connection-mode]')) button.addEventListener('click',()=>show(button.dataset.connectionMode));
  for (const button of $('connection-modes').querySelectorAll('button')) button.addEventListener('click',()=>{draft.mode=button.dataset.mode;dirty=true;render();});
  $('connection-close').addEventListener('click',()=>{dialog.hidden=true;$('connection-plug-password').value='';$('connection-tesla-return').value='';});
  $('connection-reload').addEventListener('click',()=>{load();message('Current setup loaded.');});
  $('connection-save').addEventListener('click',save);
  for (const id of ['connection-plug-address','connection-plug-source','connection-virtual-model']) $(id).addEventListener('input',()=>{dirty=true;if(id==='connection-plug-source')draft.smartPlug.vehicleSource=$(id).value;render();});
  for (const id of ['connection-virtual-battery','connection-virtual-plugged']) $(id).addEventListener('input',()=>{dirty=true;carDirty=true;render();});
  $('connection-plug-check').addEventListener('click',async()=>{
    if (dirty && !(await save())) return;
    const password=$('connection-plug-password').value;
    try { if(await send('/api/connections/plug/check',password ? {password} : {})) message('Shelly device and power meter verified. Outlet power was not changed.'); }
    finally {$('connection-plug-password').value='';}
  });
  for (const [id,on] of [['connection-plug-on',true],['connection-plug-off',false]]) $(id).addEventListener('click',async()=>{
    if (!confirm(`Switch the real Shelly outlet ${on ? 'ON' : 'OFF'}? This changes power to the connected device.`)) return;
    if(await send('/api/connections/plug/power',{on})) message('Outlet command accepted. See the observed relay status above.');
  });
  function showQR(url) {
    $('connection-tesla-link').value=url;$('connection-tesla-link-box').hidden=false;
    const qr=qrcode(0,'M');qr.addData(url);qr.make();
    const count=qr.getModuleCount(), scale=Math.max(2,Math.floor(320/(count+8)));
    const canvas=$('connection-tesla-qr');canvas.width=canvas.height=(count+8)*scale;
    const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.fillStyle='#000';
    for(let row=0;row<count;row++)for(let col=0;col<count;col++)if(qr.isDark(row,col))context.fillRect((col+4)*scale,(row+4)*scale,scale,scale);
  }
  $('connection-tesla-authorize').addEventListener('click',async()=>{
    const result = await send('/api/connections/tesla/authorize',{});
    const url=result?.actionResult?.authorizationURL;
    if (!url) return;
    const parsed=new URL(url);
    if(parsed.protocol!=='https:' || parsed.hostname!=='auth.tesla.com' || parsed.username || parsed.password) {message('Tesla authorization address was invalid.');return;}
    try {
      teslaReturnURL = new URL(parsed.searchParams.get('redirect_uri'));
      if (teslaReturnURL.protocol !== 'https:' || teslaReturnURL.username || teslaReturnURL.password || teslaReturnURL.search || teslaReturnURL.hash) throw new Error();
    } catch (_) { teslaReturnURL = null; message('Tesla return address was invalid.'); return; }
    $('connection-tesla-return').value = '';
    $('connection-tesla-return-box').hidden = false;
    showQR(url);
    message('Sign in on Tesla’s website. Return to your paired iPhone, or paste the return page address below to finish here.');
  });
  $('connection-tesla-complete').addEventListener('click',async()=>{
    let callback;
    try {
      callback = new URL($('connection-tesla-return').value.trim());
      if (!teslaReturnURL || callback.origin !== teslaReturnURL.origin || callback.pathname !== teslaReturnURL.pathname
          || callback.username || callback.password || callback.hash
          || callback.searchParams.getAll('code').length !== 1 || callback.searchParams.getAll('state').length !== 1
          || !callback.searchParams.get('code') || !callback.searchParams.get('state')) throw new Error();
    } catch (_) { message('Paste the complete SPARC return page address from this Tesla sign-in.'); return; }
    const body = {code:callback.searchParams.get('code'),state:callback.searchParams.get('state')};
    // The one-use code is sent only to this Pi and never retained in the page.
    $('connection-tesla-return').value = '';
    if (await send('/api/connections/tesla/complete',body)) {
      teslaReturnURL = null;
      $('connection-tesla-return-box').hidden = true;
      $('connection-tesla-link-box').hidden = true;
      $('connection-tesla-link').value = '';
      message('Tesla authorized. Refresh vehicles, then choose your car.');
    }
  });
  $('connection-tesla-refresh').addEventListener('click',async()=>{if(await send('/api/connections/tesla/refresh',{}))message('Authorized Tesla vehicles refreshed.');});
  $('connection-tesla-select').addEventListener('click',async()=>{const vehicleId=$('connection-tesla-vehicle').value;if(vehicleId && await send('/api/connections/tesla/select',{vehicleId}))message('Selected Tesla saved on the Pi.');});
  $('connection-tesla-key').addEventListener('click',()=>{
    const url = state?.tesla.keyPairingURL;
    if (!url) return;
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.hostname !== 'tesla.com' || !parsed.pathname.startsWith('/_ak/')) return;
    showQR(url); message('Scan with your iPhone and approve SPARC’s vehicle key in the Tesla app. Then refresh vehicles to verify it.');
  });
  $('connection-tesla-activate').addEventListener('click',async()=>{
    if (!confirm('Use your real Tesla for manual charging controls? This pauses the virtual nightly test and keeps its history. It does not start or stop the car.')) return;
    if (await send('/api/connections/activate',{mode:'tesla'})) message('Tesla is active. Start and Stop now control the real car. Automatic price scheduling is not enabled for Tesla.');
  });
  $('connection-virtual-activate').addEventListener('click',async()=>{
    if (!confirm('Return to Virtual Car? The Tesla keeps its current charging state. Its controls will no longer be active, and the virtual routine stays paused.')) return;
    if (await send('/api/connections/activate',{mode:'virtual'})) message('Virtual Car is active. Review the saved routine before resuming the test.');
  });
  for (const button of document.querySelectorAll('[data-tesla-command]')) button.addEventListener('click',async()=>{
    const command = button.dataset.teslaCommand, body = {command};
    if (command === 'set_charge_limit') {
      const value = Number($('connection-tesla-limit').value);
      if (!Number.isInteger(value) || value < 50 || value > 100) { message('Choose a charge limit from 50 to 100 percent.'); return; }
      body.value = value;
    }
    if (!confirm(command === 'set_charge_limit' ? `Set the real Tesla charge limit to ${body.value}%?` : `${command === 'charge_start' ? 'Start' : 'Stop'} charging your real Tesla?`)) return;
    const result = await send('/api/connections/tesla/command',body), receipt = result?.actionResult;
    if (receipt?.accepted === true && receipt.command === command) message(receipt.warning || 'Tesla accepted the command. Refresh the car to check its observed charging state.');
  });
  for (const button of document.querySelectorAll('[data-tesla-refresh]')) button.addEventListener('click',async()=>{if(await send('/api/connections/tesla/refresh',{}))message('Tesla charging data and command readiness refreshed.');});
  $('connection-tesla-disconnect').addEventListener('click',async()=>{if(confirm('Remove Tesla authorization from this Pi?') && await send('/api/connections/tesla/disconnect',{}))message('Tesla authorization removed from the Pi.');});
  setInterval(render,5000);
})();
