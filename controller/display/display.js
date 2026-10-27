'use strict';
// The Pi's own screen. It polls the loopback API and animates between polls;
// every control is one tap, and the controller stays the source of truth.
(() => {
  const TZ = 'America/Chicago';
  const IDLE_MS = 3 * 60 * 1000;
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (id) => document.getElementById(id);
  const app = $('app');
  const clockFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit' });
  const dateFmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
  const hmFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const weekdayFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'long' });
  const DAYS = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const CHIP = { charging: 'Charging', waiting: 'Waiting', paused: 'Paused', complete: 'Ready', unplugged: 'Not plugged in', none: 'No car', loading: 'Connecting' };

  let data = null;
  let fetchedAt = 0;
  let loadedAt = 0;
  let failures = 0;
  let busy = false;
  let view = 'charge';
  let shown = 0;
  let week = null;
  let weekDirty = false;
  let trip = null;
  let tripDirty = false;
  let endArmedUntil = 0;
  let lastTouch = performance.now();
  const texts = new Map();

  if (new URLSearchParams(location.search).has('kiosk')) document.documentElement.classList.add('kiosk');

  // Formatting, always in Chicago time like the app.
  const clockParts = (date) => {
    const [time, ampm = ''] = clockFmt.format(date).split(/\s+/);
    return { time, ampm };
  };
  const timeLabel = (value) => { const p = clockParts(new Date(value)); return `${p.time} ${p.ampm}`; };
  const minutesParts = (m) => ({ time: `${Math.floor(m / 60) % 12 || 12}:${String(m % 60).padStart(2, '0')}`, ampm: m < 720 ? 'AM' : 'PM' });
  const minutesLabel = (m) => { const p = minutesParts(m); return `${p.time} ${p.ampm}`; };
  const localDate = (date) => dateFmt.format(date);
  const localMinutes = (date) => { const [h, m] = hmFmt.format(date).split(':').map(Number); return (h % 24) * 60 + m; };
  const dayNumber = (iso) => { const [y, m, d] = iso.split('-').map(Number); return Date.UTC(y, m - 1, d) / 86400000; };
  const offsetDays = (date) => dayNumber(localDate(date)) - dayNumber(localDate(new Date()));
  const relativeDay = (date) => { const off = offsetDays(date); return off === 0 ? 'today' : off === 1 ? 'tomorrow' : weekdayFmt.format(date); };
  const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);
  const duration = (minutes) => { const m = Math.max(0, Math.round(minutes)); const h = Math.floor(m / 60); return h ? `${h}h ${m % 60}m` : `${m}m`; };
  const money = (value) => `$${value.toFixed(2)}`;
  const wrap = (m) => (m + 1440) % 1440;
  const daysLabel = (days) => {
    const key = [...days].sort((a, b) => a - b).join(',');
    if (key === '1,2,3,4,5') return 'Weekdays';
    if (key === '6,7') return 'Weekends';
    if (days.length === 7) return 'Every day';
    return [...days].sort((a, b) => a - b).map((d) => DAYS[d]).join(' ');
  };
  const setText = (id, value) => {
    const text = String(value);
    if (texts.get(id) !== text) { texts.set(id, text); $(id).textContent = text; }
  };
  // The Chicago wall-clock time as an instant; two corrections settle the UTC offset.
  const chicagoInstant = (day, minutes) => {
    const [y, m, d] = day.split('-').map(Number);
    const wall = Date.UTC(y, m - 1, d, Math.floor(minutes / 60), minutes % 60);
    let guess = wall;
    for (let i = 0; i < 3; i++) {
      const date = new Date(guess);
      const [yy, mm, dd] = localDate(date).split('-').map(Number);
      guess += wall - (Date.UTC(yy, mm - 1, dd) + localMinutes(date) * 60000);
    }
    return guess;
  };

  // State
  const snapshot = () => (data && data.snapshot && !data.snapshot.controlOnly ? data.snapshot : null);
  const carState = () => {
    if (!data) return 'loading';
    if (!data.snapshot) return 'none';
    return data.snapshot.controlOnly ? 'unplugged' : data.snapshot.state;
  };
  const hasGoal = () => !!(data && (data.trip || (data.weekly.enabled && data.weekly.days.length)));
  const skipped = () => !!(data && data.manual && data.manual.state === 'waiting');
  const demoOn = () => !!(data && data.snapshot && data.snapshot.virtualCar === true);

  async function poll() {
    try {
      const response = await fetch('/api/state', { cache: 'no-store' });
      if (!response.ok) throw new Error(String(response.status));
      apply(await response.json());
      failures = 0;
    } catch {
      failures += 1;
    }
    renderLink();
    setTimeout(poll, failures ? 3000 : 2000);
  }

  function apply(next) {
    const before = carState();
    data = next;
    fetchedAt = performance.now();
    const after = carState();
    app.dataset.state = after;
    if (loadedAt && before !== after) {
      if (after === 'charging') ignite();
      if (after === 'complete' && before === 'charging') celebrate();
    }
    if (!weekDirty) week = structuredClone(data.weekly);
    if (!tripDirty) trip = tripFromServer();
    if (!loadedAt) {
      loadedAt = performance.now();
      shown = 0;
      document.documentElement.dataset.ready = 'true';
      setTimeout(() => $('splash').classList.add('done'), calm ? 0 : 650);
    }
    render();
  }

  function render() {
    if (!data) return;
    renderCharge();
    renderSchedule();
    renderStatus();
    renderDev();
  }

  function renderDev() {
    const on = demoOn();
    const toggle = $('dev-demo');
    toggle.classList.toggle('on', on);
    toggle.setAttribute('aria-checked', String(on));
    toggle.disabled = busy || failures >= 2 || !data.snapshot;
    const c = data.controller || {};
    setText('dev-info', `SPARC ${c.id || ''} · running for ${duration((c.uptimeSeconds || 0) / 60)}`);
  }

  function renderLink() {
    const lost = failures >= 2;
    $('banner').hidden = !lost;
    const link = $('link');
    link.classList.toggle('ok', !lost && !!data);
    link.classList.toggle('lost', lost);
    setText('link-label', lost ? 'Reconnecting' : data ? 'Connected' : 'Connecting');
    if (lost) render();
  }

  // Charge
  function renderCharge() {
    const s = snapshot();
    const state = carState();
    const chip = $('chip');
    chip.classList.toggle('live', state === 'charging');
    chip.classList.toggle('ready', state === 'complete');
    chip.classList.toggle('warn', state === 'paused');
    setText('chip-label', CHIP[state] || 'Waiting');
    const [title, subtitle] = heading(s, state);
    setText('title', title);
    setText('subtitle', subtitle);
    setText('amb-state', CHIP[state] || '');
    $('amb-line').classList.toggle('live', state === 'charging');
    if (s) {
      $('meter-target').style.left = `${s.targetPercent}%`;
      setText('meter-caption', state === 'complete' ? `${s.targetPercent}% and ready to go` : state === 'charging' ? `Charging to ${s.targetPercent}%` : `Target ${s.targetPercent}%`);
      setText('kw', s.powerKW.toFixed(1));
      setText('power-caption', state === 'charging' ? 'Live from the charger' : state === 'paused' ? 'Paused · 0 kW' : 'Not drawing power');
      setText('left-label', `To ${s.targetPercent}%`);
      setText('left', state === 'complete' ? 'Done' : duration(s.remainingMinutes));
      if (skipped() && state === 'waiting') {
        setText('eta-label', 'Schedule');
        setText('eta', 'Skipped');
        setText('eta-caption', data.manual.until ? `Back after ${timeLabel(data.manual.until)}` : 'Charge now still works');
      } else if (hasGoal()) {
        setText('eta-label', 'Ready by');
        setText('eta', timeLabel(s.readyBy));
        setText('eta-caption', `Leave ${relativeDay(new Date(Date.parse(s.readyBy) + 30 * 60000))} ${timeLabel(Date.parse(s.readyBy) + 30 * 60000)}`);
      } else {
        setText('eta-label', 'Schedule');
        setText('eta', 'Off');
        setText('eta-caption', 'Charge now any time');
      }
    } else {
      setText('meter-caption', state === 'unplugged' ? 'Plug in to charge' : 'Waiting for the car');
      setText('kw', '0.0');
      setText('power-caption', 'Not drawing power');
      setText('left-label', 'To target');
      setText('left', '—');
      setText('eta-label', 'Ready by');
      setText('eta', '—');
      setText('eta-caption', ' ');
    }
    renderButtons(state);
  }

  function heading(s, state) {
    if (state === 'loading') return ['Connecting to SPARC', 'One moment.'];
    if (state === 'unplugged' || !s) return ['Plug in to charge', 'Your schedule is saved and ready.'];
    switch (state) {
      case 'charging': return ['Charging with good energy', `${s.powerKW.toFixed(1)} kW · ${duration(s.remainingMinutes)} to ${s.targetPercent}%`];
      case 'paused': return ['Charging paused', '0 kW · resume whenever you like'];
      case 'complete': return ['Your charge is complete', hasGoal() ? `${s.targetPercent}% · ready by ${timeLabel(s.readyBy)}` : `${s.targetPercent}% and ready to go`];
      default: {
        if (skipped()) {
          const until = data.manual.until;
          return ['Charging skipped', until ? `Back on schedule after ${relativeDay(new Date(until))} ${timeLabel(until)}` : 'Tap Charge now any time.'];
        }
        if (!hasGoal()) return ['Ready when you are', 'No schedule set. Tap Charge now any time.'];
        const start = new Date(s.windowStart);
        if (start - Date.now() > 60000) return ['Waiting for a better charging time', `Starts ${relativeDay(start)} at ${timeLabel(start)}`];
        return ['Waiting for a better charging time', `Ready by ${timeLabel(s.readyBy)}`];
      }
    }
  }

  function renderButtons(state) {
    const lost = failures >= 2;
    const schedule = ['schedule', 'Schedule', 'i-calendar', ''];
    const end = Date.now() < endArmedUntil ? ['end', 'Tap again to end', 'i-stop', 'confirm'] : ['arm-end', 'End session', 'i-stop', ''];
    let main;
    let alt = schedule;
    switch (state) {
      case 'charging': main = ['pause', 'Pause', 'i-pause', '']; alt = end; break;
      case 'paused': main = ['resume', 'Resume', 'i-play', 'go']; alt = end; break;
      case 'complete': main = [null, 'Ready to go', 'i-check', 'done']; break;
      case 'waiting': main = ['charge_now', 'Charge now', 'i-bolt', 'cta invite']; break;
      case 'unplugged': main = [null, 'Plug in to charge', 'i-plug', 'cta']; break;
      default: main = [null, 'Charge now', 'i-bolt', 'cta'];
    }
    setButton($('btn-main'), main, (!main[0] && main[3] !== 'done') || busy || lost, !main[0]);
    setButton($('btn-alt'), alt, alt[0] !== 'schedule' && (busy || lost), false);
  }

  function setButton(button, [action, label, icon, variant], disabled, inert) {
    button.dataset.action = action || '';
    const name = `btn ${variant}`.trim();
    if (button.className !== name) button.className = name;
    button.disabled = disabled;
    button.setAttribute('aria-disabled', String(disabled || inert));
    const use = button.querySelector('use');
    if (use.getAttribute('href') !== `#${icon}`) use.setAttribute('href', `#${icon}`);
    const text = button.querySelector('.txt');
    if (text.textContent !== label) text.textContent = label;
  }

  async function act(button) {
    const action = button.dataset.action;
    if (!action) return;
    if (action === 'schedule') { go('schedule'); return; }
    if (action === 'arm-end') {
      endArmedUntil = Date.now() + 4000;
      render();
      return;
    }
    endArmedUntil = 0;
    if (!(await send('/api/command', { command: action }))) return;
    const state = carState();
    if ((action === 'charge_now' || action === 'resume') && state === 'charging') {
      burst(button);
      toast(action === 'resume' ? 'Charging resumed' : `Charging started · ${snapshot().powerKW.toFixed(1)} kW`, 'i-bolt');
    } else if (state === 'complete') {
      toast('Already at your target', 'i-check');
    } else if (action === 'pause') {
      toast('Paused · 0 kW', 'i-pause');
    } else if (action === 'end') {
      toast('Session ended · back on your schedule', 'i-check');
    }
  }

  async function send(path, body) {
    if (busy) return false;
    busy = true;
    render();
    try {
      const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store' });
      const reply = await response.json().catch(() => null);
      if (!response.ok || !reply || !reply.ok) throw new Error((reply && reply.error) || 'SPARC could not do that. Try again.');
      busy = false;
      apply(reply);
      return true;
    } catch (error) {
      toast(error instanceof TypeError ? 'SPARC is reconnecting. Try again in a moment.' : error.message, 'i-alert', true);
      return false;
    } finally {
      busy = false;
      render();
    }
  }

  // Schedule
  function tripFromServer() {
    if (data.trip) {
      const when = new Date(data.trip.departure);
      return { day: offsetDays(when), minutes: localMinutes(when), target: data.trip.targetPercent };
    }
    const minutes = data.weekly.departureMinutes;
    return { day: localMinutes(new Date()) < minutes ? 0 : 1, minutes, target: data.weekly.targetPercent };
  }

  const tripValid = () => trip && (trip.day === 1 || (trip.day === 0 && trip.minutes > localMinutes(new Date())));

  function renderSchedule() {
    if (!week || !trip) return;
    const lost = failures >= 2;
    for (const b of document.querySelectorAll('#trip-day button')) b.classList.toggle('on', Number(b.dataset.day) === trip.day);
    const tp = minutesParts(trip.minutes);
    setText('trip-time', tp.time);
    setText('trip-ampm', tp.ampm);
    markTargets('trip', trip.target);
    $('trip-clear').hidden = !data.trip;
    setText('trip-sub', data.trip
      ? `Saved · ${capital(relativeDay(new Date(data.trip.departure)))} ${timeLabel(data.trip.departure)} · ${data.trip.targetPercent}%`
      : 'Just once. Your routine stays.');
    const ok = tripValid();
    $('trip-save').disabled = busy || lost || !ok || (!tripDirty && !!data.trip);
    $('trip-save').classList.toggle('invite', tripDirty && ok);
    setText('trip-hint', ok ? '' : 'Pick a later time, or choose Tomorrow.');

    for (const b of document.querySelectorAll('#week-days .day')) b.classList.toggle('on', week.days.includes(Number(b.dataset.day)));
    const wp = minutesParts(week.departureMinutes);
    setText('week-time', wp.time);
    setText('week-ampm', wp.ampm);
    const fp = minutesParts(week.fixedStartMinutes);
    setText('fixed-time', fp.time);
    setText('fixed-ampm', fp.ampm);
    markTargets('week', week.targetPercent);
    for (const b of document.querySelectorAll('#week-mode button')) b.classList.toggle('on', b.dataset.mode === week.mode);
    $('week-fixed').hidden = week.mode !== 'fixed';
    const toggle = $('week-enabled');
    toggle.classList.toggle('on', week.enabled);
    toggle.setAttribute('aria-checked', String(week.enabled));
    $('week-card').classList.toggle('is-off', !week.enabled);
    const weekOk = !week.enabled || week.days.length > 0;
    $('week-save').disabled = busy || lost || !weekDirty || !weekOk;
    $('week-save').classList.toggle('invite', weekDirty && weekOk);
    $('week-undo').hidden = !weekDirty;
    setText('week-sub', week.enabled
      ? `${daysLabel(week.days) || 'No days'} · leave ${minutesLabel(week.departureMinutes)} · ${week.targetPercent}%`
      : 'Paused. Charge now still works.');
    setText('week-hint', weekOk ? '' : 'Pick at least one day.');
  }

  function markTargets(group, value) {
    for (const b of document.querySelectorAll(`[data-targets="${group}"] .target`)) b.classList.toggle('on', Number(b.dataset.target) === value);
  }

  async function saveTrip(button) {
    const body = { dayOffset: trip.day, departureMinutes: trip.minutes, targetPercent: trip.target };
    if (!(await send('/api/trip', body))) return;
    tripDirty = false;
    trip = tripFromServer();
    burst(button);
    toast(`Trip saved · ${capital(relativeDay(new Date(data.trip.departure)))} ${timeLabel(data.trip.departure)} · ${data.trip.targetPercent}%`);
    renderSchedule();
  }

  async function clearTrip() {
    if (!(await send('/api/trip', { clear: true }))) return;
    tripDirty = false;
    trip = tripFromServer();
    toast('Trip cleared · your routine is back');
    renderSchedule();
  }

  async function saveWeek(button) {
    if (!(await send('/api/weekly', week))) return;
    weekDirty = false;
    week = structuredClone(data.weekly);
    burst(button);
    toast(week.enabled ? `Routine saved · ${daysLabel(week.days)} · ${minutesLabel(week.departureMinutes)} · ${week.targetPercent}%` : 'Routine paused');
    renderSchedule();
  }

  // Status
  function renderStatus() {
    const c = data.controller || {};
    setText('st-id', c.id || '');
    setText('st-online', failures >= 2 ? 'Reconnecting' : 'Online');
    setText('st-uptime', `Running for ${duration((c.uptimeSeconds || 0) / 60)}`);
    if (c.phoneSeconds == null) {
      setText('st-phone', 'Not yet');
      setText('st-phone-sub', 'Open SPARC on your iPhone nearby');
    } else {
      setText('st-phone', c.phoneSeconds < 60 ? 'Connected' : `${duration(c.phoneSeconds / 60)} ago`);
      setText('st-phone-sub', c.phoneTransport === 'lan' ? 'Last seen on Wi-Fi' : 'Last seen over Bluetooth');
    }
    setText('st-bt', c.bluetooth ? 'On' : 'Off');
    $('st-bt').classList.toggle('green', !!c.bluetooth);
    setText('st-bt-sub', c.bluetooth ? 'Your iPhone can connect nearby' : 'Starting Bluetooth…');
    const t = c.temperatureC;
    setText('st-temp', t == null ? '—' : `${Math.round(t)}°C`);
    setText('st-temp-sub', t == null ? 'Not available' : t < 70 ? 'Running cool' : t < 80 ? 'Warm' : 'Hot · check airflow');
    const s = snapshot();
    setText('st-rate', s ? `${Math.round(s.currentRateCents)}¢` : '—');
    setText('st-saved', s ? money(s.sessionSavings) : '—');
    renderRates();
    renderPlan(s);
  }

  function renderRates() {
    const bars = $('rate-bars');
    const hour = Math.floor(localMinutes(new Date()) / 60);
    if (bars.dataset.hour === String(hour)) return;
    bars.dataset.hour = String(hour);
    bars.replaceChildren(...Array.from({ length: 24 }, (_, h) => {
      const bar = document.createElement('span');
      const cheap = h >= 1 && h < 5;
      bar.className = h === hour ? 'now' : cheap ? 'cheap' : '';
      bar.style.height = cheap ? '38%' : '100%';
      return bar;
    }));
  }

  function renderPlan(s) {
    const track = $('plan-track');
    const now = Date.now();
    const charging = carState() === 'charging';
    const departure = s && hasGoal() ? Date.parse(s.readyBy) + 30 * 60000 : now + 12 * 3600000;
    const end = Math.max(departure, now + 3600000);
    const span = end - now;
    const at = (t) => `${Math.max(0, Math.min(100, ((t - now) / span) * 100)).toFixed(2)}%`;
    const key = [Math.floor(now / 60000), s && s.windowStart, s && s.windowEnd, departure, carState(), skipped()].join('|');
    if (track.dataset.key === key) return;
    track.dataset.key = key;
    const parts = [];
    const add = (className, from, to) => {
      const el = document.createElement('div');
      el.className = className;
      el.style.left = at(from);
      if (to != null) el.style.width = `calc(${at(to)} - ${at(from)})`;
      parts.push(el);
    };
    for (let d = 0; d <= 2; d++) {
      const day = localDate(new Date(now + d * 86400000));
      const a = chicagoInstant(day, 60);
      const b = chicagoInstant(day, 300);
      if (b > now && a < end) add('tl-cheap', Math.max(a, now), Math.min(b, end));
    }
    let windowText = skipped() ? 'Skipped tonight' : hasGoal() ? 'Nothing left to charge' : 'No schedule tonight';
    if (s && charging) {
      const to = Math.min(end, now + Math.max(1, s.remainingMinutes) * 60000);
      add('tl-charge', now, to);
      windowText = `Now – ${timeLabel(to)}`;
    } else if (s && hasGoal() && !skipped() && s.remainingMinutes > 0 && carState() === 'waiting') {
      const from = Math.max(now, Date.parse(s.windowStart));
      const to = Math.min(end, Math.max(Date.parse(s.windowEnd), from + 60000));
      add('tl-charge', from, to);
      windowText = `${timeLabel(from)} – ${timeLabel(to)}`;
    }
    add('tl-mark now', now);
    if (hasGoal()) add('tl-mark', departure);
    track.replaceChildren(...parts);
    setText('plan-window', windowText);
    const scale = $('plan-scale');
    const labels = [['Now', now]];
    for (let d = 0; d <= 2; d++) {
      const day = localDate(new Date(now + d * 86400000));
      for (const m of [60, 300]) {
        const t = chicagoInstant(day, m);
        if (t > now + span * 0.08 && t < end - span * 0.08) labels.push([minutesLabel(m).replace(':00', ''), t]);
      }
    }
    labels.push([hasGoal() ? `Leave ${timeLabel(departure)}` : timeLabel(end), end]);
    scale.replaceChildren(...labels.map(([text, t]) => {
      const el = document.createElement('span');
      el.textContent = text;
      el.style.left = at(t);
      return el;
    }));
  }

  // Live counters between polls; easing follows elapsed time, so a slow panel still settles on time.
  let lastFrame = 0;
  function frame(now) {
    const step = lastFrame ? Math.min(1, (now - lastFrame) / 1000) : 0;
    lastFrame = now;
    const s = snapshot();
    if (s) {
      const seconds = (now - fetchedAt) / 1000;
      const charging = carState() === 'charging';
      let battery = data.batteryPercent != null ? data.batteryPercent : s.batteryPercent;
      if (charging) battery = Math.min(s.targetPercent, battery + seconds * data.batteryPercentPerHour / 3600);
      shown = calm ? battery : shown + (battery - shown) * (1 - Math.exp(-step / (now - loadedAt < 2500 ? 0.45 : 0.12)));
      if (Math.abs(battery - shown) < 0.003) shown = battery;
      const whole = Math.floor(shown + 1e-6);
      setText('soc-int', whole);
      setText('soc-dec', `.${Math.min(9, Math.floor((shown - whole) * 10 + 1e-6))}`);
      setText('amb-soc', `${whole}%`);
      const fill = `${Math.max(0, Math.min(100, shown)).toFixed(2)}%`;
      if (texts.get('fill') !== fill) { texts.set('fill', fill); $('meter-fill').style.width = fill; }
      const extra = charging ? seconds * s.powerKW / 3600 : 0;
      setText('added', (s.energyKWh + extra).toFixed(charging ? 3 : 2));
      setText('cost', money(s.estimatedCost + extra * s.currentRateCents / 100));
    } else if (data) {
      setText('soc-int', '--');
      setText('soc-dec', '');
      setText('amb-soc', '--%');
      setText('added', '—');
      setText('cost', '—');
      if (texts.get('fill') !== '0%') { texts.set('fill', '0%'); $('meter-fill').style.width = '0%'; }
    }
    requestAnimationFrame(frame);
  }

  // Delight: light bar, confetti, celebration, toasts.
  function ignite() {
    app.classList.remove('ignite');
    void app.offsetWidth;
    app.classList.add('ignite');
    setTimeout(() => app.classList.remove('ignite'), 1300);
  }

  const canvas = $('confetti');
  const ctx = canvas.getContext('2d');
  const COLORS = ['#52E39B', '#2E8B63', '#FFFFFF', '#BDF5D7', '#8A8A8A', '#3FBF7F'];
  let pieces = [];
  let lastStep = 0;
  function fitCanvas() {
    const ratio = window.devicePixelRatio || 1;
    canvas.width = innerWidth * ratio;
    canvas.height = innerHeight * ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }
  function spawn(x, y, count, spread, power) {
    if (calm) return;
    for (let i = 0; i < count; i++) {
      const angle = -Math.PI / 2 + (Math.random() - 0.5) * spread;
      const speed = power * (0.55 + Math.random() * 0.6);
      pieces.push({ x, y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed, r: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 14, w: 5 + Math.random() * 5, h: 8 + Math.random() * 7,
        c: COLORS[i % COLORS.length], round: i % 3 === 0, life: 0, max: 1.7 + Math.random() * 1.1 });
    }
    if (pieces.length === count) { lastStep = performance.now(); requestAnimationFrame(step); }
  }
  function step(now) {
    const dt = Math.min(0.033, (now - lastStep) / 1000);
    lastStep = now;
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    pieces = pieces.filter((p) => (p.life += dt) < p.max && p.y < innerHeight + 40);
    for (const p of pieces) {
      p.vy += 1100 * dt;
      p.vx *= 0.985;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.r += p.vr * dt;
      ctx.save();
      ctx.globalAlpha = Math.min(1, (p.max - p.life) * 2.5);
      ctx.translate(p.x, p.y);
      ctx.rotate(p.r);
      ctx.fillStyle = p.c;
      const h = Math.max(1, p.h * Math.abs(Math.cos(p.r * 1.3)));
      if (p.round) { ctx.beginPath(); ctx.ellipse(0, 0, p.w / 2, h / 2, 0, 0, Math.PI * 2); ctx.fill(); }
      else ctx.fillRect(-p.w / 2, -h / 2, p.w, h);
      ctx.restore();
    }
    if (pieces.length) requestAnimationFrame(step);
    else ctx.clearRect(0, 0, innerWidth, innerHeight);
  }
  function burst(element) {
    const rect = element.getBoundingClientRect();
    spawn(rect.left + rect.width / 2, rect.top + rect.height / 2, 60, Math.PI * 0.85, Math.max(560, innerHeight * 1.25));
  }
  function celebrate() {
    const s = snapshot();
    if (!s) return;
    setText('cel-pct', `${s.targetPercent}%`);
    setText('cel-sub', hasGoal() ? `Charged to ${s.targetPercent}% · ready by ${timeLabel(s.readyBy)}` : `Charged to ${s.targetPercent}%`);
    const layer = $('celebrate');
    layer.hidden = true;
    void layer.offsetWidth;
    layer.hidden = false;
    for (let i = 0; i < 6; i++) spawn(innerWidth * (i + 0.5) / 6, innerHeight * 0.62, 26, Math.PI * 0.7, innerHeight * 1.55);
    clearTimeout(celebrate.timer);
    celebrate.timer = setTimeout(() => { layer.hidden = true; }, 6000);
  }
  function toast(text, icon = 'i-check', warn = false) {
    const element = $('toast');
    $('toast-text').textContent = text;
    $('toast-icon').setAttribute('href', `#${icon}`);
    element.classList.toggle('warn', warn);
    element.classList.remove('show');
    void element.offsetWidth;
    element.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => element.classList.remove('show'), 2800);
  }

  // Navigation and input
  function go(next) {
    if (next === view) return;
    view = next;
    app.dataset.view = next;
    for (const tab of document.querySelectorAll('.tab')) tab.classList.toggle('is-active', tab.dataset.go === next);
    for (const name of ['charge', 'schedule', 'status']) $(`view-${name}`).hidden = name !== next;
    if (next === 'schedule' && data) {
      if (!weekDirty) week = structuredClone(data.weekly);
      if (!tripDirty) trip = tripFromServer();
      renderSchedule();
    }
  }

  function stepper(button, change) {
    let hold = 0;
    let repeat = 0;
    const stop = () => { clearTimeout(hold); clearInterval(repeat); };
    button.addEventListener('pointerdown', (event) => {
      if (!week || !trip) return;
      event.preventDefault();
      stop();
      change();
      hold = setTimeout(() => { repeat = setInterval(change, 110); }, 450);
    });
    for (const type of ['pointerup', 'pointerleave', 'pointercancel']) button.addEventListener(type, stop);
    button.addEventListener('click', (event) => { if (event.detail === 0 && week && trip) change(); });
  }
  const editTrip = (fn) => () => { fn(); tripDirty = true; renderSchedule(); };
  const editWeek = (fn) => () => { fn(); weekDirty = true; renderSchedule(); };
  stepper($('trip-earlier'), editTrip(() => { trip.minutes = wrap(trip.minutes - 15); }));
  stepper($('trip-later'), editTrip(() => { trip.minutes = wrap(trip.minutes + 15); }));
  stepper($('week-earlier'), editWeek(() => { week.departureMinutes = wrap(week.departureMinutes - 15); }));
  stepper($('week-later'), editWeek(() => { week.departureMinutes = wrap(week.departureMinutes + 15); }));
  stepper($('fixed-earlier'), editWeek(() => { week.fixedStartMinutes = wrap(week.fixedStartMinutes - 15); }));
  stepper($('fixed-later'), editWeek(() => { week.fixedStartMinutes = wrap(week.fixedStartMinutes + 15); }));

  const pick = (selector, handler) => document.querySelector(selector).addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (button && week && trip) handler(button);
  });
  pick('#trip-day', (b) => editTrip(() => { trip.day = Number(b.dataset.day); })());
  pick('[data-targets="trip"]', (b) => editTrip(() => { trip.target = Number(b.dataset.target); })());
  pick('[data-targets="week"]', (b) => editWeek(() => { week.targetPercent = Number(b.dataset.target); })());
  pick('#week-mode', (b) => editWeek(() => { week.mode = b.dataset.mode; })());
  pick('#week-days', (b) => {
    const day = Number(b.dataset.day);
    editWeek(() => { week.days = week.days.includes(day) ? week.days.filter((d) => d !== day) : [...week.days, day].sort((a, c) => a - c); })();
    b.classList.remove('pop');
    void b.offsetWidth;
    b.classList.add('pop');
  });
  $('week-enabled').addEventListener('click', () => { if (week) editWeek(() => { week.enabled = !week.enabled; })(); });
  $('week-undo').addEventListener('click', () => { weekDirty = false; week = structuredClone(data.weekly); renderSchedule(); });
  $('week-save').addEventListener('click', (event) => saveWeek(event.currentTarget));
  $('trip-save').addEventListener('click', (event) => saveTrip(event.currentTarget));
  $('trip-clear').addEventListener('click', clearTrip);
  $('btn-main').addEventListener('click', (event) => act(event.currentTarget));
  $('btn-alt').addEventListener('click', (event) => act(event.currentTarget));
  for (const tab of document.querySelectorAll('.tab')) tab.addEventListener('click', () => go(tab.dataset.go));
  $('celebrate').addEventListener('click', () => { $('celebrate').hidden = true; });

  // Developer sheet, hidden like the app's: seven quick taps on the logo.
  let brandTaps = [];
  $('brand').addEventListener('click', () => {
    const now = Date.now();
    brandTaps = brandTaps.filter((t) => now - t < 3000).concat(now);
    if (brandTaps.length >= 7 && data) { brandTaps = []; $('dev').hidden = false; renderDev(); }
  });
  $('dev-close').addEventListener('click', () => { $('dev').hidden = true; });
  $('dev-demo').addEventListener('click', async () => {
    const enabled = !demoOn();
    if (await send('/api/demo', { enabled })) toast(enabled ? 'Demo car on · plugged in at 40%' : 'Demo car off', enabled ? 'i-bolt' : 'i-check');
  });
  $('ambient').addEventListener('click', () => { $('ambient').hidden = true; lastTouch = performance.now(); });

  document.addEventListener('pointerdown', (event) => {
    lastTouch = performance.now();
    const target = event.target.closest('.btn, .round, .tab, .day, .target, .seg button');
    if (!target || target.disabled) return;
    const rect = target.getBoundingClientRect();
    const dot = document.createElement('span');
    dot.className = 'ripple';
    dot.style.left = `${event.clientX - rect.left}px`;
    dot.style.top = `${event.clientY - rect.top}px`;
    target.append(dot);
    dot.addEventListener('animationend', () => dot.remove());
  }, true);
  document.addEventListener('contextmenu', (event) => event.preventDefault());
  window.addEventListener('resize', fitCanvas);

  // A quiet clock after three idle minutes; it drifts slowly so the panel never holds one image.
  setInterval(() => {
    const ambient = $('ambient');
    if (!ambient.hidden) {
      if (Math.random() < 0.08) $('amb-inner').style.transform = `translate(${((Math.random() - 0.5) * 8).toFixed(1)}vw, ${((Math.random() - 0.5) * 8).toFixed(1)}vh)`;
      return;
    }
    if (loadedAt && $('celebrate').hidden && performance.now() - lastTouch > IDLE_MS) ambient.hidden = false;
  }, 5000);

  function tickClock() {
    const p = clockParts(new Date());
    setText('clock', p.time);
    setText('ampm', p.ampm);
    setText('amb-time', p.time);
    setText('amb-ampm', p.ampm);
    if (endArmedUntil && Date.now() > endArmedUntil) { endArmedUntil = 0; render(); }
  }

  fitCanvas();
  if (['schedule', 'status'].includes(location.hash.slice(1))) go(location.hash.slice(1));
  tickClock();
  setInterval(tickClock, 1000);
  setTimeout(() => $('splash').classList.add('done'), 8000);
  requestAnimationFrame(frame);
  poll();
})();
