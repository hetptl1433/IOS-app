'use strict';
// Recorded values keep a missing price distinct from a real zero or credit.
const SavingsFormat = (() => {
  const known = (value) => typeof value === 'number' && Number.isFinite(value);
  const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
  const money = (value) => known(value) ? currency.format(value) : '—';
  const energy = (value) => known(value) ? value.toFixed(1) : '—';
  const percent = (value) => known(value) ? `${Math.abs(value).toFixed(1)}%` : '—';
  const signedPercent = (value) => known(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}%` : '—';
  const comparison = (record, daily = false) => {
    const empty = record.sessionCount === 0 || (known(record.energyKWh) && Math.abs(record.energyKWh) <= 1e-9);
    const canDerive = known(record.savingsUSD) && known(record.baselineCostUSD) && Math.abs(record.baselineCostUSD) > 1e-9;
    // Weekly percentages come from the total baseline, never the average of daily percentages.
    const value = empty || (daily && !(record.sessionCount > 0)) ? null
      : known(record.savingsPercent) ? record.savingsPercent
      : canDerive ? 100 * record.savingsUSD / Math.abs(record.baselineCostUSD) : null;
    return { value, tone: empty ? 'empty' : !known(value) ? 'unknown' : value < 0 ? 'extra' : 'saved',
      label: empty ? 'No charging' : known(value) ? value < 0 ? 'Higher cost' : value > 0 ? 'Saved' : 'No savings'
        : !known(record.savingsUSD) || !known(record.baselineCostUSD) ? 'Awaiting prices' : 'Comparison unavailable' };
  };
  const saving = (value, percentage) => !known(value) ? 'Comparison pending'
    : `${value < 0 ? 'Extra cost' : 'Saved'} ${money(Math.abs(value))}${known(percentage) ? ` · ${percent(percentage)}` : ''}`;
  const addDays = (day, amount) => {
    const date = new Date(`${day}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + amount);
    return date.toISOString().slice(0, 10);
  };
  const campaignBody = (value) => Object.fromEntries([
    'enabled', 'startDate', 'nights', 'startMinutes', 'departureMinutes',
    'initialBatteryPercent', 'targetBatteryPercent', 'screenSleepMinutes', 'expectedRevision'
  ].map((key) => [key, value[key]]));
  const validCampaign = (value) => {
    if (!value || typeof value.enabled !== 'boolean' || !/^\d{4}-\d{2}-\d{2}$/.test(value.startDate)) return false;
    const parsed = new Date(`${value.startDate}T12:00:00Z`);
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value.startDate) return false;
    const limits = { nights: [1,90], startMinutes: [0,1439], departureMinutes: [0,1439],
      initialBatteryPercent: [0,99], targetBatteryPercent: [50,100], screenSleepMinutes: [1,120], expectedRevision: [0,Number.MAX_SAFE_INTEGER] };
    return Object.entries(limits).every(([key,[min,max]]) => Number.isInteger(value[key]) && value[key] >= min && value[key] <= max)
      && value.initialBatteryPercent < value.targetBatteryPercent;
  };
  return { known, money, energy, percent, signedPercent, comparison, saving, addDays, campaignBody, validCampaign };
})();
// The Pi's own screen. It polls the loopback API and animates between polls;
// every control is one tap, and the controller stays the source of truth.
(() => {
  const TZ = 'America/Chicago';
  const IDLE_MS = 15 * 60 * 1000;
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
  let campaign = null;
  let campaignDirty = false;
  let developerDraft = null;
  let developerDirty = false;
  let historyData = null;
  let historySessions = [];
  let historyNextCursor = null;
  let historyLoading = false;
  let historyError = false;
  let historyAttempted = false;
  let historyLoadedAt = 0;
  let selectedDay = null;
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
    if (data) renderStatus();
    setTimeout(poll, failures ? 3000 : 2000);
  }

  function apply(next) {
    const before = carState();
    data = next;
    document.dispatchEvent(new CustomEvent('sparc-state', { detail: next }));
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
    if (!historyAttempted) loadHistory();
  }

  let night = null;
  let nightDirty = false;

  function render() {
    if (!data) return;
    renderCharge();
    renderSchedule();
    renderNight();
    renderCampaign();
    renderStatus();
    renderDev();
  }

  function nightFromServer() {
    const config = data && data.overnight;
    if (!config) return null;
    const start = new Date(config.start);
    const end = new Date(config.departure);
    const today = localDate(new Date());
    const offset = dayNumber(localDate(start)) - dayNumber(today);
    return { dayOffset: offset, startMinutes: localMinutes(start), departureMinutes: localMinutes(end),
      initialBatteryPercent: config.initialBatteryPercent, targetPercent: config.targetBatteryPercent,
      screenSleepMinutes: config.screenSleepMinutes || 5 };
  }

  function renderNight() {
    const active = !!(data && data.overnight);
    $('night-card').hidden = !active;
    $('trip-card').hidden = active || !!data.campaign;
    $('week-card').hidden = active || !!data.campaign;
    $('view-schedule').classList.toggle('overnight-mode', active);
    if (!active) return;
    if (!nightDirty || !night) night = nightFromServer();
    setText('night-start', minutesLabel(night.startMinutes));
    setText('night-ready', minutesLabel(night.departureMinutes));
    setText('night-target', `${night.targetPercent}%`);
    setText('night-initial', `${night.initialBatteryPercent}%`);
    setText('night-sleep', `${night.screenSleepMinutes} min`);
    const day = night.dayOffset < 0 ? 'Last night' : night.dayOffset === 1 ? 'Tomorrow' : 'Tonight';
    setText('night-summary', `${day} · ${minutesLabel(night.startMinutes)} → ${minutesLabel(night.departureMinutes)} · Chicago time`);
    const locked = busy || failures >= 2 || !demoOn();
    const started = Date.now() >= Date.parse(data.overnight.start) || (snapshot() && snapshot().energyKWh > 0);
    for (const field of ['start', 'ready', 'target', 'initial', 'sleep']) {
      for (const direction of ['less', 'more']) $(`night-${field}-${direction}`).disabled = locked || (field === 'initial' && started);
    }
    for (const button of $('night-day').querySelectorAll('button')) {
      button.classList.toggle('on', Number(button.dataset.day) === night.dayOffset);
      button.disabled = locked;
    }
    $('night-save').disabled = locked || !nightDirty;
    $('night-undo').hidden = !nightDirty;
    setText('night-hint', data.campaign ? 'Only this night · edit Nightly routine above to change the remaining nights.' : started ? 'Starting battery is locked after charging starts. Times and target can still change.'
      : nightDirty ? 'Unsaved changes · tap Save changes to apply' : data.overnight.strategy === 'day_ahead_live' ? 'Saved · day-ahead + live prices · replans every 5 minutes · logs recording' : 'Saved · real ComEd prices · simulated car · logs recording');
  }

  function editNight(change) {
    if (!night || busy) return;
    change(); nightDirty = true; renderNight();
  }

  async function saveNight(button) {
    if (!night || !nightDirty || busy) return;
    if (!(await send('/api/overnight', night))) return;
    nightDirty = false; night = nightFromServer();
    render(); burst(button); toast('Overnight settings saved');
  }

  const recordDateFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric' });
  const recordDayFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short' });
  const recordDate = (value) => new Date(/^\d{4}-\d{2}-\d{2}$/.test(value || '') ? `${value}T12:00:00Z` : value);
  const dateLabel = (value) => Number.isFinite(recordDate(value).getTime()) ? recordDateFmt.format(recordDate(value)) : '—';
  const safeTime = (value) => Number.isFinite(Date.parse(value)) ? timeLabel(value) : '—';
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  function campaignFromServer() {
    if (!data || !data.campaign) return null;
    const value = data.campaign;
    return SavingsFormat.campaignBody({ ...value, expectedRevision: value.revision });
  }

  function renderCampaign() {
    const saved = data && data.campaign;
    $('campaign-card').hidden = !saved;
    $('view-schedule').classList.toggle('campaign-mode', !!saved);
    if (!saved) {
      setText('savings-routine', data && data.overnight
        ? `${safeTime(data.overnight.start)} → ${safeTime(data.overnight.departure)} · ${data.overnight.targetBatteryPercent}% target`
        : 'No nightly routine saved');
      return;
    }
    if (!campaignDirty || !campaign) campaign = campaignFromServer();
    const lastDay = SavingsFormat.addDays(campaign.startDate, campaign.nights - 1);
    const finalDay = SavingsFormat.addDays(lastDay, campaign.departureMinutes <= campaign.startMinutes ? 1 : 0);
    setText('campaign-summary', `${campaign.enabled ? 'Every night' : 'Paused'} · ${dateLabel(campaign.startDate)}–${dateLabel(lastDay)} · Chicago time`);
    const current = saved.current;
    const phase = saved.phase === 'complete' ? 'Routine has ended · see Savings for recorded results.'
      : !saved.enabled ? 'Routine paused · save changes to turn it back on.'
      : saved.nextStart ? `Next night · ${dateLabel(saved.nextStart)} at ${safeTime(saved.nextStart)}`
      : current ? `Current night · ${dateLabel(current.start)} · ${safeTime(current.start)} → ${safeTime(current.departure)}`
      : 'Your routine is saved on SPARC.';
    setText('campaign-current', phase);
    setText('campaign-start', minutesLabel(campaign.startMinutes));
    setText('campaign-ready', minutesLabel(campaign.departureMinutes));
    setText('campaign-target', `${campaign.targetBatteryPercent}%`);
    setText('campaign-initial', `${campaign.initialBatteryPercent}%`);
    setText('campaign-date', dateLabel(campaign.startDate));
    setText('campaign-nights', String(campaign.nights));
    setText('campaign-sleep', `${campaign.screenSleepMinutes} min`);
    setText('campaign-end', `${dateLabel(finalDay)} · ${minutesLabel(campaign.departureMinutes)}`);
    const locked = busy || failures >= 2;
    const toggle = $('campaign-enabled');
    toggle.classList.toggle('on', campaign.enabled);
    toggle.setAttribute('aria-checked', String(campaign.enabled));
    toggle.disabled = locked;
    for (const button of $('campaign-card').querySelectorAll('.round')) button.disabled = locked;
    $('campaign-save').disabled = locked || !campaignDirty || !SavingsFormat.validCampaign(campaign);
    $('campaign-undo').hidden = !campaignDirty;
    setText('campaign-undo', 'Undo');
    setText('campaign-hint', campaignDirty ? 'Unsaved changes · Save routine applies your values. Latest saved command wins.'
      : 'Latest saved command wins. Other phones update automatically. Starting battery changes apply to future nights after a night starts.');
    setText('savings-routine', `${saved.enabled ? 'Every night' : 'Paused'} · ${minutesLabel(saved.startMinutes)} → ${minutesLabel(saved.departureMinutes)} · ${saved.initialBatteryPercent}% → ${saved.targetBatteryPercent}% · ${saved.nights} nights`);
  }

  function editCampaign(change) {
    if (!campaign || busy || failures >= 2) return;
    change(); campaignDirty = true; renderCampaign();
  }

  async function saveCampaign(button) {
    if (!campaignDirty || !SavingsFormat.validCampaign(campaign) || busy) return;
    if (!(await send('/api/campaign', SavingsFormat.campaignBody(campaign)))) return;
    campaignDirty = false; campaign = campaignFromServer();
    render(); burst(button); toast(campaign.enabled ? 'Nightly routine saved' : 'Nightly routine paused');
  }

  async function loadHistory(append = false) {
    if (historyLoading) return;
    const cursor = append ? historyNextCursor : 0;
    if (append && cursor == null) return;
    historyLoading = true; historyAttempted = true;
    $('history-refresh').disabled = true;
    $('history-more').disabled = true;
    try {
      const response = await fetch(`/api/history?cursor=${cursor}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(String(response.status));
      const payload = await response.json();
      if (!payload.week || !Array.isArray(payload.sessions) || !Array.isArray(payload.week.days)) throw new Error('History unavailable');
      historyData = payload;
      historySessions = append ? [...new Map([...historySessions, ...payload.sessions].map((value) => [value.id, value])).values()] : payload.sessions;
      historyNextCursor = Number.isInteger(payload.nextCursor) && payload.nextCursor > cursor ? payload.nextCursor : null;
      historyLoadedAt = Date.now(); historyError = false;
    } catch {
      historyError = true;
    } finally {
      historyLoading = false;
      $('history-refresh').disabled = false;
      $('history-more').disabled = false;
      renderSavings();
      if (data) renderStatus();
    }
  }

  function renderSavings() {
    if (!historyData) {
      $('session-list').replaceChildren(element('p', 'history-empty', historyError
        ? 'History is unavailable. Tap Refresh to try again.' : 'No recorded charging yet.'));
      setText('history-update', historyError ? 'Unable to load saved history' : 'Real ComEd prices · simulated car');
      return;
    }
    const week = historyData.week;
    setText('savings-range', `${dateLabel(week.start)}–${dateLabel(SavingsFormat.addDays(week.end, -1))} · Chicago time`);
    setText('savings-energy', SavingsFormat.energy(week.energyKWh));
    setText('savings-cost', SavingsFormat.money(week.costUSD));
    setText('savings-cost-label', SavingsFormat.known(week.costUSD) ? 'Cost this week' : 'Cost awaiting prices');
    const comparison = SavingsFormat.comparison(week);
    const extra = comparison.tone === 'extra';
    setText('savings-label', extra ? 'Higher cost this week' : 'Savings this week');
    setText('savings-percent', SavingsFormat.signedPercent(comparison.value));
    setText('savings-amount', SavingsFormat.known(comparison.value)
      ? `${SavingsFormat.money(SavingsFormat.known(week.savingsUSD) ? Math.abs(week.savingsUSD) : null)} ${extra ? 'higher cost' : 'saved'} · vs immediate charging`
      : comparison.tone === 'empty' ? 'No charging recorded this week' : comparison.label);
    $('savings-total-card').classList.toggle('is-extra', extra);
    $('savings-total-card').classList.toggle('is-unknown', !SavingsFormat.known(comparison.value));
    const coverage = week.unpricedSessions > 0 ? `${week.unpricedSessions} session comparison${week.unpricedSessions === 1 ? '' : 's'} awaiting prices · incomplete totals show —.`
      : week.provisional ? 'Provisional prices · totals will update as ComEd settles prices.'
      : 'Recorded ComEd electricity supply cost · simulated car.';
    setText('history-price-note', coverage);
    setText('history-update', historyError ? 'Refresh failed · showing the last loaded history'
      : `Updated ${timeLabel(historyLoadedAt)} · ${week.provisional ? 'provisional prices' : 'real ComEd prices'} · simulated car`);
    setText('history-count', `${historySessions.length} session${historySessions.length === 1 ? '' : 's'}`);
    renderEnergyChart(week.days);
    renderDailySavings(week.days);
    const openIds = new Set([...$('session-list').querySelectorAll('details[open]')].map((node) => node.dataset.session));
    $('session-list').replaceChildren(...(historySessions.length ? historySessions.map((session) => sessionCard(session, openIds.has(session.id)))
      : [element('p', 'history-empty', 'No recorded sessions yet. Your first run will appear here.')]));
    $('history-more').hidden = historyNextCursor == null;
  }

  function renderDailySavings(days) {
    $('daily-savings').replaceChildren(...days.map((day) => {
      const comparison = SavingsFormat.comparison(day, true);
      const button = element('button', `daily-saving ${comparison.tone}${day.date === selectedDay ? ' selected' : ''}`);
      button.type = 'button';
      const weekday = recordDayFmt.format(recordDate(day.date));
      const value = SavingsFormat.signedPercent(comparison.value);
      button.setAttribute('aria-pressed', String(day.date === selectedDay));
      button.setAttribute('aria-label', `${weekday} ${dateLabel(day.date)} starting night, ${value}, ${comparison.label}`);
      button.append(element('span', 'daily-name', weekday), element('span', 'daily-date', dateLabel(day.date)),
        element('span', 'daily-percent num', value), element('span', 'daily-result', comparison.label));
      button.addEventListener('click', () => { selectedDay = day.date; renderDailySavings(days); renderEnergyChart(days); });
      return button;
    }));
  }

  function renderEnergyChart(days) {
    const maximum = Math.max(1, ...days.map((day) => SavingsFormat.known(day.energyKWh) ? day.energyKWh : 0));
    if (!days.some((day) => day.date === selectedDay)) {
      selectedDay = (days.find((day) => day.date === localDate(new Date())) || [...days].reverse().find((day) => day.sessionCount > 0) || days[0] || {}).date;
    }
    $('energy-chart').replaceChildren(...days.map((day) => {
      const button = element('button', `energy-day${day.date === selectedDay ? ' selected' : ''}`);
      button.type = 'button';
      button.setAttribute('aria-pressed', String(day.date === selectedDay));
      button.setAttribute('aria-label', `${dateLabel(day.date)}, ${SavingsFormat.energy(day.energyKWh)} kWh, cost ${SavingsFormat.money(day.costUSD)}`);
      button.append(element('span', 'energy-value num', SavingsFormat.energy(day.energyKWh)));
      const space = element('span', 'energy-bar-space');
      const bar = element('span', `energy-bar${!SavingsFormat.known(day.energyKWh) || day.energyKWh === 0 ? ' empty' : ''}`);
      bar.style.height = `${SavingsFormat.known(day.energyKWh) ? Math.max(0,day.energyKWh) / maximum * 100 : 0}%`;
      bar.setAttribute('aria-hidden', 'true'); space.append(bar); button.append(space);
      button.append(element('span', 'day-name', recordDayFmt.format(recordDate(day.date))),
                    element('span', 'day-cost num', SavingsFormat.money(day.costUSD)));
      button.addEventListener('click', () => { selectedDay = day.date; renderDailySavings(days); renderEnergyChart(days); });
      return button;
    }));
    const selected = days.find((day) => day.date === selectedDay);
    const comparison = selected && SavingsFormat.comparison(selected, true);
    setText('day-detail', selected ? `${recordDayFmt.format(recordDate(selected.date))} ${dateLabel(selected.date)} starting night · ${SavingsFormat.energy(selected.energyKWh)} kWh · Cost ${SavingsFormat.money(selected.costUSD)} · ${SavingsFormat.signedPercent(comparison.value)} ${comparison.label.toLowerCase()}` : 'No daily observations yet.');
  }

  function sessionCard(session, opened) {
    const card = element('details', 'session-card');
    card.dataset.session = session.id; card.open = opened;
    const summary = element('summary');
    const title = element('div');
    const date = element('p', 'session-date', dateLabel(session.nightDate || session.date || session.start));
    const status = { scheduled:'Scheduled', active:'In progress', complete:'Completed', incomplete:'Incomplete' }[session.status] || 'Recorded';
    date.append(element('span', 'session-badge', status));
    title.append(date, element('p', 'session-sub num', `${safeTime(session.start)} → ${safeTime(session.departure)} · ${SavingsFormat.energy(session.energyKWh)} kWh`));
    const amounts = element('div');
    const comparison = SavingsFormat.comparison(session);
    amounts.append(element('p', `session-saving num ${comparison.tone}`, `${SavingsFormat.signedPercent(comparison.value)} ${comparison.label.toLowerCase()}`),
      element('p', 'session-amount num', `Cost ${SavingsFormat.money(session.costUSD)}`));
    summary.append(title, amounts, element('span', 'session-chevron', '⌄'));
    const detail = element('div', 'session-detail');
    const metrics = element('dl', 'session-metrics num');
    for (const [label,value] of [
      ['Starting battery', SavingsFormat.known(session.initialBatteryPercent) ? `${session.initialBatteryPercent}%` : '—'],
      ['Target / recorded', `${SavingsFormat.known(session.targetBatteryPercent) ? session.targetBatteryPercent : '—'}% / ${SavingsFormat.known(session.batteryPercent) ? session.batteryPercent.toFixed(1) : '—'}%`],
      ['Recorded cost', SavingsFormat.money(session.costUSD)],
      ['Immediate charging', SavingsFormat.money(session.baselineCostUSD)]
    ]) { const group = element('div'); group.append(element('dt','',label),element('dd','',value)); metrics.append(group); }
    const deadline = session.targetReachedByDeadline === true ? 'Target reached by departure.'
      : session.targetReachedByDeadline === false ? 'Target was not reached by departure.' : session.status === 'scheduled' || session.status === 'active' ? 'Run not yet finished.' : 'Deadline result unavailable.';
    const prices = !SavingsFormat.known(session.costUSD) ? 'Cost awaiting recorded prices.'
      : session.provisional || session.costFinal === false || session.baselineFinal === false ? 'Provisional comparison; awaiting settled prices.'
      : session.costFinal === true && session.baselineFinal === true ? 'Settled ComEd prices.' : 'Recorded ComEd prices.';
    detail.append(metrics, element('p', 'session-sub', `${SavingsFormat.saving(session.savingsUSD, comparison.value)}. ${deadline} ${prices}${SavingsFormat.known(session.unpricedEnergyKWh) && session.unpricedEnergyKWh > 0 ? ` ${SavingsFormat.energy(session.unpricedEnergyKWh)} kWh awaiting prices.` : ''} Simulated car.`));
    card.append(summary, detail);
    return card;
  }

  function developerFromServer() {
    const value = data && data.developer;
    if (!value || value.enabled !== true || value.simulated !== true || !SavingsFormat.known(value.batteryPercent)
        || typeof value.pluggedIn !== 'boolean') return null;
    return { batteryPercent: value.batteryPercent, pluggedIn: value.pluggedIn };
  }

  function renderDev() {
    const live = developerFromServer();
    if (!developerDirty) developerDraft = live;
    const locked = busy || failures >= 2 || !live;
    const value = developerDraft;
    setText('settings-car-state', live ? `${live.batteryPercent.toFixed(1)}% · ${live.pluggedIn ? 'plugged in' : 'unplugged'} · simulated car` : 'Current car state unavailable');
    setText('dev-info', 'Simulated car · real ComEd prices');
    setText('dev-live', live ? `${failures >= 2 ? 'Last received' : 'Live now'} · ${live.batteryPercent.toFixed(1)}% · ${live.pluggedIn ? 'plugged in' : 'unplugged'}` : 'Current car state unavailable');
    setText('dev-battery-value', value ? `${Number.isInteger(value.batteryPercent) ? value.batteryPercent : value.batteryPercent.toFixed(1)}%` : '—');
    if (value) $('dev-battery-slider').value = String(value.batteryPercent);
    $('dev-battery-slider').disabled = locked;
    $('dev-battery-less').disabled = locked || !value || value.batteryPercent <= 0;
    $('dev-battery-more').disabled = locked || !value || value.batteryPercent >= 100;
    const plugged = !!(value && value.pluggedIn);
    $('dev-plugged').classList.toggle('on', plugged);
    $('dev-plugged').setAttribute('aria-checked', String(plugged));
    $('dev-plugged').disabled = locked;
    setText('dev-plugged-caption', plugged ? 'Connected to the charger' : 'Disconnected from the charger');
    $('dev-apply').disabled = locked || !developerDirty || !value;
    $('dev-load-live').disabled = locked;
    setText('dev-hint', !live ? 'Reconnect to SPARC to change the current car state.'
      : developerDirty ? 'Unsaved car values · Apply sets this battery and plug state. Your routine and saved history stay.'
      : 'Apply changes only the current car state. Your routine and saved history stay.');
  }

  function editDeveloper(change) {
    if (!developerDraft || busy || failures >= 2 || !developerFromServer()) return;
    change(); developerDirty = true; renderDev();
  }

  async function applyDeveloper(button) {
    if (!developerDraft || !developerDirty || busy) return;
    const values = { batteryPercent: developerDraft.batteryPercent, pluggedIn: developerDraft.pluggedIn };
    if (!(await send('/api/developer', values))) return;
    developerDirty = false; developerDraft = developerFromServer();
    render(); toast('Current car state applied'); burst(button); loadHistory();
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
      setText('power-caption', state === 'charging' ? (s.virtualCar ? 'Simulated charging power' : 'Live from the charger') : state === 'paused' ? 'Paused · 0 kW' : 'Not drawing power');
      setText('left-label', `To ${s.targetPercent}%`);
      setText('left', state === 'complete' ? 'Done' : duration(s.remainingMinutes));
      if (skipped() && state === 'waiting') {
        setText('eta-label', 'Schedule');
        setText('eta', 'Skipped');
        setText('eta-caption', data.manual.until ? `Back after ${timeLabel(data.manual.until)}` : 'Charge now still works');
      } else if (hasGoal()) {
        setText('eta-label', 'Ready by');
        setText('eta', timeLabel(data.overnight ? data.overnight.departure : s.readyBy));
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
    if (s.smartCharging && !data.manual && data.optimizer) {
      const plan = data.optimizer;
      if (state === 'charging') return ['Smart charging', `Following the price plan · ${duration(s.remainingMinutes)} of charging left`];
      if (state === 'waiting') {
        const next = s.nextChargeAt ? timeLabel(s.nextChargeAt) : 'the next price check';
        return [plan.fallback ? 'Protecting your morning target' : 'Waiting for lower-cost hours',
          `Next planned charge ${next} · plan updates every 5 minutes`];
      }
    }
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
        if (start - Date.now() > 60000) return [data.weekly.mode === 'fixed' ? 'Waiting for your start time' : 'Waiting for a better charging time', `Starts ${relativeDay(start)} at ${timeLabel(start)}`];
        return [data.weekly.mode === 'fixed' ? 'Waiting for your start time' : 'Waiting for a better charging time', `Ready by ${timeLabel(s.readyBy)}`];
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
    const wifi = c.wifi;
    setText('st-online', failures >= 2 ? 'Reconnecting' : wifi && !wifi.connected ? 'No Wi-Fi' : 'Online');
    setText('st-uptime', !wifi ? `Running for ${duration((c.uptimeSeconds || 0) / 60)}`
      : wifi.connected ? (wifi.ssid ? `Wi-Fi · ${wifi.ssid}` : 'On your network')
      : 'Set up Wi-Fi from Status in the iPhone app');
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
    const pricing = data.pricing || {};
    const rateLive = failures < 2 && pricing.status === 'live' && Number.isFinite(pricing.rateCents)
      && Number.isFinite(Date.parse(pricing.observedAt))
      && Date.now() - Date.parse(pricing.observedAt) <= 20 * 60000;
    setText('st-rate', rateLive ? `${pricing.rateCents.toFixed(1)}¢` : '—');
    setText('st-rate-sub', rateLive ? 'ComEd · current hour avg · ¢/kWh' : 'ComEd price unavailable');
    const recorded = historySessions.find((session) => SavingsFormat.known(session.savingsUSD));
    setText('st-saved', recorded ? SavingsFormat.money(Math.abs(recorded.savingsUSD)) : '—');
    setText('st-saved-label', recorded && recorded.savingsUSD < 0 ? 'Latest extra cost' : 'Latest recorded saving');
    setText('st-saved-sub', recorded ? `${dateLabel(recorded.nightDate || recorded.date || recorded.start)} · electricity supply only` : 'Awaiting a recorded comparison');
    $('st-saved').classList.toggle('green', !!recorded && recorded.savingsUSD >= 0);
    const paired = data.clients && data.clients.pairedCount;
    setText('st-clients', Number.isInteger(paired) ? `${paired} app${paired === 1 ? '' : 's'} remembered` : 'App count unavailable');
    const control = data.control;
    const operationNames = { set_developer:'Car state applied', set_developer_state:'Car state applied', set_virtual_car_state:'Car state applied',
      set_campaign:'Routine saved', set_overnight:'Current night saved', set_weekly:'Schedule saved',
      set_today:'Trip saved', set_trip:'Trip saved', charge_now:'Charge now', pause:'Charging paused',
      resume:'Price plan resumed', end:'Session ended' };
    setText('st-command', control && Number.isInteger(control.sequence)
      ? `Latest change #${control.sequence} · ${operationNames[control.operation] || 'Controller updated'}` : 'No saved command recorded');
    setText('st-command-client', control && control.updatedAt
      ? `${dateLabel(control.updatedAt)} ${safeTime(control.updatedAt)} · ${control.clientName || 'Controller'}${control.clientId ? ` · ${control.clientId}` : ''}` : 'Nearby apps show the latest controller state.');
    renderPlan(s);
  }

  function renderPlan(s) {
    const track = $('plan-track');
    const now = Date.now();
    const charging = carState() === 'charging';
    const smart = !!(s && s.smartCharging && data.optimizer);
    const departure = smart ? Date.parse(data.overnight.departure) : s && hasGoal() ? Date.parse(s.readyBy) + 30 * 60000 : now + 12 * 3600000;
    const end = Math.max(departure, now + 3600000);
    const span = end - now;
    const at = (t) => `${Math.max(0, Math.min(100, ((t - now) / span) * 100)).toFixed(2)}%`;
    const key = [Math.floor(now / 60000), s && s.windowStart, s && s.windowEnd, departure, carState(), skipped(), data.optimizer && data.optimizer.generatedAt].join('|');
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
    let windowText = skipped() ? 'Skipped tonight' : hasGoal() ? 'Nothing left to charge' : 'No schedule tonight';
    if (smart) {
      const intervals = data.optimizer.intervals || [];
      const pending = intervals.filter((part) => Date.parse(part.end) > now);
      for (const part of pending) {
        const from = Math.max(now, Date.parse(part.start));
        const to = Math.min(end, Date.parse(part.end));
        if (to > from) add('tl-charge', from, to);
      }
      windowText = skipped() ? 'Paused by you' : pending.length
        ? `${timeLabel(Math.max(now, Date.parse(pending[0].start)))} – ${timeLabel(pending[pending.length - 1].end)} · updates every 5 min`
        : 'Target reached';
    } else if (s && charging) {
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
      setText('cost', s.costAvailable === false ? '—' : money(s.estimatedCost + (s.rateAvailable === false ? 0 : extra * s.currentRateCents / 100)));
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
    for (const name of ['charge', 'schedule', 'savings', 'status']) $(`view-${name}`).hidden = name !== next;
    if (next === 'schedule' && data) {
      if (!weekDirty) week = structuredClone(data.weekly);
      if (!tripDirty) trip = tripFromServer();
      renderSchedule();
      renderCampaign();
    }
    if (next === 'savings') {
      renderSavings();
      if (!historyData || historyError || Date.now() - historyLoadedAt > 30000) loadHistory();
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

  const nightChanges = {
    start: (delta) => { night.startMinutes = wrap(night.startMinutes + delta * 15); },
    ready: (delta) => { night.departureMinutes = wrap(night.departureMinutes + delta * 15); },
    target: (delta) => { night.targetPercent = Math.max(50, Math.min(100, night.targetPercent + delta * 5)); },
    initial: (delta) => { night.initialBatteryPercent = Math.max(0, Math.min(95, night.initialBatteryPercent + delta * 5)); },
    sleep: (delta) => { night.screenSleepMinutes = Math.max(1, Math.min(120, night.screenSleepMinutes + delta * 5)); }
  };
  for (const [field, change] of Object.entries(nightChanges)) {
    stepper($(`night-${field}-less`), () => editNight(() => change(-1)));
    stepper($(`night-${field}-more`), () => editNight(() => change(1)));
  }
  $('night-day').addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (button && !button.disabled) editNight(() => { night.dayOffset = Number(button.dataset.day); });
  });
  $('night-save').addEventListener('click', (event) => saveNight(event.currentTarget));
  $('night-undo').addEventListener('click', () => { nightDirty = false; night = nightFromServer(); renderNight(); });

  const campaignChanges = {
    start: (delta) => { campaign.startMinutes = wrap(campaign.startMinutes + delta * 15); },
    ready: (delta) => { campaign.departureMinutes = wrap(campaign.departureMinutes + delta * 15); },
    target: (delta) => { campaign.targetBatteryPercent = Math.max(50, campaign.initialBatteryPercent + 1, Math.min(100, campaign.targetBatteryPercent + delta * 5)); },
    initial: (delta) => { campaign.initialBatteryPercent = Math.max(0, Math.min(campaign.targetBatteryPercent - 1, campaign.initialBatteryPercent + delta * 5)); },
    date: (delta) => { campaign.startDate = SavingsFormat.addDays(campaign.startDate, delta); },
    nights: (delta) => { campaign.nights = Math.max(1, Math.min(90, campaign.nights + delta)); },
    sleep: (delta) => { campaign.screenSleepMinutes = Math.max(1, Math.min(120, campaign.screenSleepMinutes + delta)); }
  };
  for (const [field, change] of Object.entries(campaignChanges)) {
    stepper($(`campaign-${field}-less`), () => editCampaign(() => change(-1)));
    stepper($(`campaign-${field}-more`), () => editCampaign(() => change(1)));
  }
  $('campaign-enabled').addEventListener('click', () => editCampaign(() => { campaign.enabled = !campaign.enabled; }));
  $('campaign-save').addEventListener('click', (event) => saveCampaign(event.currentTarget));
  $('campaign-undo').addEventListener('click', () => { campaignDirty = false; campaign = campaignFromServer(); renderCampaign(); });
  $('history-refresh').addEventListener('click', () => loadHistory());
  $('history-more').addEventListener('click', () => loadHistory(true));
  $('savings-edit-routine').addEventListener('click', () => { go('schedule'); $('view-schedule').scrollTop = 0; });
  $('settings-history').addEventListener('click', () => { go('savings'); $('history-count').scrollIntoView({block:'start'}); });

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

  // Developer car state is reachable directly from Settings, with explicit Apply.
  $('settings-dev').addEventListener('click', () => { $('dev').hidden = false; renderDev(); $('dev-close').focus(); });
  $('dev-close').addEventListener('click', () => { $('dev').hidden = true; });
  stepper($('dev-battery-less'), () => editDeveloper(() => { developerDraft.batteryPercent = Math.max(0, Math.round(developerDraft.batteryPercent) - 1); }));
  stepper($('dev-battery-more'), () => editDeveloper(() => { developerDraft.batteryPercent = Math.min(100, Math.round(developerDraft.batteryPercent) + 1); }));
  $('dev-battery-slider').addEventListener('input', (event) => editDeveloper(() => { developerDraft.batteryPercent = Math.max(0, Math.min(100, Number(event.target.value))); }));
  $('dev-plugged').addEventListener('click', () => editDeveloper(() => { developerDraft.pluggedIn = !developerDraft.pluggedIn; }));
  $('dev-load-live').addEventListener('click', () => { developerDirty = false; developerDraft = developerFromServer(); renderDev(); });
  $('dev-apply').addEventListener('click', (event) => applyDeveloper(event.currentTarget));
  document.addEventListener('sparc-connections-updated', (event) => apply(event.detail));
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
  if (['schedule', 'savings', 'status'].includes(location.hash.slice(1))) go(location.hash.slice(1));
  tickClock();
  setInterval(tickClock, 1000);
  setTimeout(() => $('splash').classList.add('done'), 8000);
  requestAnimationFrame(frame);
  poll();
  setInterval(() => {
    if (view === 'savings' && !historyLoading && $('view-savings').scrollTop < 120) loadHistory();
  }, 30000);
})();
