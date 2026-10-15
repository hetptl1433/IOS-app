'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { datasets: [], models: [], artifacts: [], runs: [], result: null, policy: 'ready', selectedModels: new Set(), selectedDataset: null, chartSequence: 0 };
  const zone = 'America/Chicago';
  const palette = ['#178879', '#759c45', '#ca9b48', '#6788b2', '#bb7592', '#9380ad', '#75a39b'];
  const colors = { charge_now: '#a3b098', 'charge-now': '#a3b098', oracle: '#9a83b4', 'seasonal-naive': '#2a8f7d', 'day-ahead': '#c49b49', ridge: '#6f93b5', lightgbm: '#779e42', 'chronos-bolt': '#b8738c', 'kronos-mean': '#7d80ac' };
  const configFields = ['battery_kwh', 'initial_soc', 'target_soc', 'charger_kw', 'delivery_cents', 'reserve_minutes', 'min_session_minutes', 'replan_minutes', 'forecast_context_hours'];
  const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const number = (value, decimals = 1) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : '—';
  const money = (value) => Number.isFinite(Number(value)) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value)) : '—';
  const isOracle = (strategy) => strategy.id === 'oracle' || /perfect|oracle/i.test(strategy.label || '');
  const modelColor = (id, index = 0) => colors[id] || palette[index % palette.length];
  const formatDate = (value, options = {}) => {
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('en-US', { timeZone: zone, month: 'short', day: 'numeric', ...options }).format(date) : '—';
  };
  const formatTime = (value) => formatDate(value, { month: undefined, day: undefined, hour: 'numeric', minute: '2-digit' });
  const formatStamp = (value) => formatDate(value, { hour: 'numeric', minute: '2-digit' });
  const dateParts = (date) => Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date).filter((p) => p.type !== 'literal').map((p) => [p.type, p.value]));
  function toLocalInput(value) {
    if (!value) return '';
    if (/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(value)) return value;
    const d = new Date(value); if (!Number.isFinite(d.getTime())) return '';
    const p = dateParts(d); return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
  }
  function chicagoISO(local) {
    const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local);
    if (!parts) throw new Error('Enter a valid arrival and departure time.');
    if (Number(parts[5]) % 5) throw new Error('Use five-minute boundaries for arrival and departure, such as 6:00 or 6:05.');
    const nominal = Date.UTC(...[Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]), Number(parts[4]), Number(parts[5])]);
    const candidates = [5, 6].map((hours) => new Date(nominal + hours * 3600000)).filter((d) => toLocalInput(d.toISOString()) === local);
    if (!candidates.length) throw new Error('That Chicago time does not exist because the clocks move forward. Choose another time.');
    // On the autumn clock change, use the first occurrence consistently.
    return candidates[0].toISOString();
  }
  function loadPreferences() { try { return JSON.parse(localStorage.getItem('sparc-lab-preferences') || '{}'); } catch { return {}; } }
  function savePreferences() {
    try {
      localStorage.setItem('sparc-lab-preferences', JSON.stringify({ dataset_id: $('dataset-select').value, model_ids: [...document.querySelectorAll('.model-choice input:checked')].map((n) => n.value), config: readRawForm(), last_run_id: state.result?.id || null }));
    } catch { /* Private browsing may disable local storage. Server history still persists. */ }
  }
  function readRawForm() {
    const config = { arrival: $('arrival').value, departure: $('departure').value, price_policy: state.policy, efficiency: Number($('efficiency').value) / 100, max_price_cents: $('max_price_cents').value === '' ? null : Number($('max_price_cents').value) };
    for (const key of configFields) config[key] = Number($(key).value);
    return config;
  }
  function apiError(message) { $('global-error').textContent = message; $('global-error').classList.remove('hidden'); }
  function clearError() { $('global-error').classList.add('hidden'); }
  function toast(message) { $('toast').textContent = message; $('toast').classList.remove('hidden'); clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 6000); }
  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
    let data; try { data = await response.json(); } catch { throw new Error(`The service returned an unreadable response (${response.status}).`); }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }
  const post = (path, data) => api(path, { method: 'POST', body: JSON.stringify(data) });
  async function pollJob(jobId, callback) {
    const started = Date.now();
    for (;;) {
      const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (job.status === 'complete') return job.result;
      if (job.status === 'failed') throw new Error(job.error || 'The experiment failed.');
      callback?.(job.status, Math.floor((Date.now() - started) / 1000));
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
  function setPage(name) {
    const page = ['simulator', 'data', 'models', 'experiments'].includes(name) ? name : 'simulator';
    document.querySelectorAll('.page-section').forEach((section) => section.classList.toggle('hidden', section.id !== `page-${page}`));
    document.querySelectorAll('[data-page]').forEach((link) => { link.classList.toggle('active', link.dataset.page === page); if (link.dataset.page === page) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
    $('breadcrumb-page').textContent = { simulator: 'Simulator', data: 'Data library', models: 'Model bench', experiments: 'Experiments' }[page];
    hideTooltip();
  }
  function setPolicy(value) {
    state.policy = value === 'strict' ? 'strict' : 'ready';
    document.querySelectorAll('[data-policy]').forEach((button) => { const active = button.dataset.policy === state.policy; button.classList.toggle('active', active); button.setAttribute('aria-pressed', active); });
    $('policy-description').textContent = state.policy === 'strict' ? 'Never intentionally exceed the predicted ceiling; a shortfall is possible. Final billing prices may differ.' : 'Prioritize the target; exceed the preferred price if needed. Final billing prices may differ.';
    $('max_price_cents').required = state.policy === 'strict';
  }
  function updateEstimate() {
    const initial = Number($('initial_soc').value), target = Number($('target_soc').value), battery = Number($('battery_kwh').value), power = Number($('charger_kw').value), efficiency = Number($('efficiency').value) / 100;
    $('initial-soc-display').textContent = initial; $('target-soc-display').textContent = target;
    $('soc-track-fill').style.width = `${Math.min(100, Math.max(0, initial))}%`;
    const needed = Math.max(0, (target - initial) * battery / 100), minutes = Math.ceil(needed / (power * efficiency) * 60);
    $('energy-estimate').lastElementChild.textContent = power > 0 && efficiency > 0 ? `${number(needed)} kWh into battery · about ${Math.floor(minutes / 60)} h ${minutes % 60} min` : 'Enter your battery and charger details.';
  }
  function applyConfig(config = {}) {
    for (const key of configFields) if (config[key] !== undefined) $(key).value = config[key];
    if (config.arrival) $('arrival').value = toLocalInput(config.arrival);
    if (config.departure) $('departure').value = toLocalInput(config.departure);
    if (config.efficiency !== undefined) $('efficiency').value = Math.round(config.efficiency * 100);
    if (config.max_price_cents !== undefined) $('max_price_cents').value = config.max_price_cents ?? '';
    setPolicy(config.price_policy); updateEstimate();
  }
  function renderDatasetOptions() {
    const selected = $('dataset-select').value || state.selectedDataset;
    const trainingSelected = $('training-dataset').value;
    const options = state.datasets.map((dataset) => `<option value="${escapeHTML(dataset.id)}">${escapeHTML(dataset.name)}${dataset.is_synthetic ? ' · SYNTHETIC' : ''}</option>`).join('');
    $('dataset-select').innerHTML = options; $('training-dataset').innerHTML = options;
    if (state.datasets.some((d) => d.id === selected)) $('dataset-select').value = selected;
    if (state.datasets.some((d) => d.id === trainingSelected)) $('training-dataset').value = trainingSelected;
    updateDatasetCaption();
  }
  function updateDatasetCaption() {
    const dataset = state.datasets.find((d) => d.id === $('dataset-select').value);
    $('dataset-caption').innerHTML = dataset ? `<span class="badge ${dataset.is_synthetic ? 'synthetic' : 'real'}">${dataset.is_synthetic ? 'SYNTHETIC' : 'HISTORICAL'}</span>${number(dataset.hourly_count || 0, 0)} complete hours · ${escapeHTML(formatDate(dataset.start))}–${escapeHTML(formatDate(dataset.end))}${!dataset.has_day_ahead ? '<br>Day-ahead history is not available for this dataset.' : ''}` : 'No datasets available. Add a dataset in the Data library.';
  }
  function renderModelChoices() {
    const current = [...document.querySelectorAll('.model-choice input:checked')].map((n) => n.value);
    if (current.length) state.selectedModels = new Set(current);
    $('model-choices').innerHTML = state.models.map((model) => `<label class="model-choice ${model.available ? '' : 'disabled'}" title="${escapeHTML(model.available ? model.description : model.reason || 'Unavailable')}"><input type="checkbox" value="${escapeHTML(model.id)}" ${model.available && state.selectedModels.has(model.id) ? 'checked' : ''} ${model.available ? '' : 'disabled'}>${escapeHTML(model.label)}${model.available ? '' : ' ↗'}</label>`).join('');
    const trainable = state.models.filter((model) => ['ridge', 'lightgbm', 'chronos-bolt'].includes(model.id));
    const previous = $('training-model').value;
    $('training-model').innerHTML = trainable.map((model) => `<option value="${escapeHTML(model.id)}">${escapeHTML(model.label)}</option>`).join('');
    if (trainable.some((m) => m.id === previous)) $('training-model').value = previous;
    $('train-button').disabled = trainable.length === 0;
  }
  function renderDataLibrary() {
    $('data-library').innerHTML = state.datasets.map((dataset) => `<article class="panel dataset-card"><div class="card-top"><span class="card-symbol" aria-hidden="true">▤</span><span class="badge ${dataset.is_synthetic ? 'synthetic' : 'real'}">${dataset.is_synthetic ? 'SYNTHETIC' : 'HISTORICAL'}</span></div><h3>${escapeHTML(dataset.name)}</h3><p>${escapeHTML(dataset.description || 'Electricity prices for forecasting and simulation.')}</p><div class="card-meta">${number(dataset.point_count, 0)} price observations · ${number(dataset.hourly_count, 0)} complete hours<br>${escapeHTML(formatDate(dataset.start, { year: 'numeric' }))} – ${escapeHTML(formatDate(dataset.end, { year: 'numeric' }))}<br>${dataset.has_day_ahead ? 'Day-ahead prices with availability timestamps' : 'No usable day-ahead forecast vintages'}${dataset.warnings?.length ? `<br><span class="dataset-warning">${dataset.warnings.length} data note${dataset.warnings.length === 1 ? '' : 's'}</span>` : ''}</div><div class="card-actions"><button class="text-button" type="button" data-preview="${escapeHTML(dataset.id)}">Preview prices ↗</button><button class="text-button" type="button" data-use-dataset="${escapeHTML(dataset.id)}">Use in simulator →</button></div></article>`).join('');
  }
  function renderModelCatalog() {
    $('model-catalog').innerHTML = state.models.map((model) => `<article class="panel model-card ${model.available ? '' : 'unavailable'}"><div class="card-top"><span class="card-symbol" aria-hidden="true">${model.id === 'day-ahead' ? '⌁' : model.id === 'seasonal-naive' ? '◷' : '⌘'}</span><span class="badge ${model.available ? 'available' : 'unavailable'}">${model.available ? 'AVAILABLE' : 'SETUP REQUIRED'}</span></div><h3>${escapeHTML(model.label)}</h3><p>${escapeHTML(model.description)}</p><div class="card-meta">${escapeHTML(String(model.kind || 'forecast model').replace(/_/g, ' '))} ${model.requires_training ? '· Local training' : '· No training required'}${!model.available ? `<div class="model-state-note">${escapeHTML(model.reason || 'Install the optional model dependencies or train an artifact. See the repository README for setup.')}</div>` : ''}</div></article>`).join('');
  }
  function renderArtifacts() {
    $('artifacts-list').innerHTML = state.artifacts.length ? state.artifacts.map((artifact) => `<div class="artifact-row"><div><strong>${escapeHTML(artifact.model_id || artifact.label || artifact.id || 'Trained model')}</strong><small>${escapeHTML(artifact.id || artifact.name || 'Local artifact')}</small></div><div><small>TRAINED THROUGH</small><strong>${escapeHTML(formatStamp(artifact.trained_until))}</strong></div><div><small>VALIDATION MAE</small><span class="artifact-value">${artifact.validation_mae !== undefined ? `${number(artifact.validation_mae, 2)} ¢/kWh` : artifact.metrics?.validation_mae !== undefined ? `${number(artifact.metrics.validation_mae, 2)} ¢/kWh` : 'See training metadata'}</span></div><span class="badge trained">SAVED LOCALLY</span></div>`).join('') : '<div class="artifact-empty">Your trained models will appear here.<br>Start with ridge regression for a small, reproducible baseline.</div>';
  }
  function renderHistory() {
    $('history-count').textContent = state.runs.length; $('runs-count').textContent = `${state.runs.length} RUN${state.runs.length === 1 ? '' : 'S'}`;
    $('history-list').innerHTML = state.runs.length ? state.runs.map((run) => {
      const summary = run.summary || {}, dataset = run.dataset || {}, config = run.config || {};
      const title = run.label || (config.arrival ? `${formatStamp(config.arrival)} → ${formatTime(config.departure)}` : `Experiment ${String(run.id).slice(0, 8)}`);
      return `<article class="history-row"><div><h3>${escapeHTML(title)}</h3><p>${escapeHTML(dataset.name || run.dataset_name || 'Saved charging comparison')}${dataset.is_synthetic || run.is_synthetic ? ' · SYNTHETIC' : ''}<br>${escapeHTML(formatStamp(run.created_at))}</p></div><div><div class="history-label">Lowest eligible cost</div><div class="history-value">${money(summary.best_cost ?? run.best_cost)}</div></div><div><div class="history-label">Savings</div><div class="history-value">${money(summary.savings ?? run.savings)}</div></div><div><div class="history-label">Strategy</div><p>${escapeHTML(summary.winner_id || run.winner_id || '—')}</p></div><div class="history-actions"><button type="button" class="button secondary" data-open-run="${escapeHTML(run.id)}">View →</button><a class="button secondary" href="/api/runs/${encodeURIComponent(run.id)}/export.csv" download aria-label="Export this experiment as CSV">↓</a></div></article>`;
    }).join('') : '<div class="history-empty">Your experiments live here.<br>Run a comparison to save the first result.</div>';
  }
  function renderAll() { renderDatasetOptions(); renderModelChoices(); renderDataLibrary(); renderModelCatalog(); renderArtifacts(); renderHistory(); }
  async function refreshBootstrap() {
    const data = await api('/api/bootstrap');
    state.datasets = data.datasets || []; state.models = data.models || []; state.artifacts = data.artifacts || []; state.runs = data.recent_runs || [];
    renderAll(); return data;
  }
  async function refreshHistory() { const data = await api('/api/runs'); state.runs = data.runs || []; renderHistory(); }
  function hideTooltip() { $('chart-tooltip').classList.add('hidden'); }
  function showTooltip(event, title, entries) {
    const tooltip = $('chart-tooltip'); tooltip.innerHTML = `<strong>${escapeHTML(title)}</strong>${entries.map(([label, value]) => `<div><span>${escapeHTML(label)}</span><b>${escapeHTML(value)}</b></div>`).join('')}`;
    tooltip.classList.remove('hidden');
    const bounds = tooltip.getBoundingClientRect(), anchor = event.clientX === undefined ? event.target.getBoundingClientRect() : { left: event.clientX, top: event.clientY };
    tooltip.style.left = `${Math.max(8, Math.min(window.innerWidth - bounds.width - 8, anchor.left + 12))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(window.innerHeight - bounds.height - 8, anchor.top + 12))}px`;
  }
  function chart(containerId, points, series, { unit = '', fixedRange, target } = {}) {
    const container = $(containerId); const valid = points.filter((p) => Number.isFinite(new Date(p.timestamp).getTime()));
    if (!valid.length) { container.innerHTML = '<div class="empty-inline">No chart observations are available for this experiment.</div>'; return; }
    const width = 680, height = 235, left = 40, right = 17, top = 17, bottom = 31, innerWidth = width - left - right, innerHeight = height - top - bottom;
    const xs = valid.map((p) => new Date(p.timestamp).getTime()), minX = Math.min(...xs), maxX = Math.max(...xs), span = maxX - minX || 3600000;
    const values = valid.flatMap((point) => series.map((s) => s.value(point)).filter((v) => v !== null && v !== undefined && Number.isFinite(Number(v))).map(Number));
    if (target !== undefined) values.push(target);
    let minY = fixedRange ? fixedRange[0] : Math.min(0, ...values), maxY = fixedRange ? fixedRange[1] : Math.max(1, ...values);
    const padding = (maxY - minY) * .12 || 1; if (!fixedRange) { minY -= padding * (minY < 0 ? 1 : 0); maxY += padding; }
    const x = (time) => left + (time - minX) / span * innerWidth, y = (value) => top + (maxY - value) / (maxY - minY || 1) * innerHeight;
    const tickValues = Array.from({ length: 5 }, (_, i) => minY + (maxY - minY) * i / 4);
    const grid = tickValues.map((value) => `<line class="chart-grid-line" x1="${left}" y1="${y(value).toFixed(2)}" x2="${width - right}" y2="${y(value).toFixed(2)}"/><text class="chart-axis-text" x="${left - 8}" y="${y(value) + 3}" text-anchor="end">${number(value, fixedRange ? 0 : maxY > 100 ? 0 : 1)}</text>`).join('');
    const tickIndices = [...new Set(Array.from({ length: Math.min(6, valid.length) }, (_, i) => Math.round(i * (valid.length - 1) / (Math.min(6, valid.length) - 1 || 1))))];
    const xTicks = tickIndices.map((i) => `<text class="chart-axis-text" x="${x(xs[i])}" y="${height - 9}" text-anchor="${i === 0 ? 'start' : i === valid.length - 1 ? 'end' : 'middle'}">${escapeHTML(formatTime(valid[i].timestamp))}</text>`).join('');
    const paths = series.map((s) => {
      let path = '', penDown = false;
      valid.forEach((point, i) => { const value = s.value(point); if (value === null || value === undefined || !Number.isFinite(Number(value))) { penDown = false; return; } path += `${penDown ? 'L' : 'M'}${x(xs[i]).toFixed(2)} ${y(Number(value)).toFixed(2)} `; penDown = true; });
      return `<path d="${path}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" ${s.dashed ? 'stroke-dasharray="5 4"' : ''} stroke-linecap="round" stroke-linejoin="round"/>`;
    }).join('');
    const targetLine = target !== undefined ? `<line x1="${left}" y1="${y(target)}" x2="${width - right}" y2="${y(target)}" stroke="#b4c5a5" stroke-width="1" stroke-dasharray="4 5"/>` : '';
    const chartId = `chart-${++state.chartSequence}`;
    const hits = valid.map((point, i) => {
      const prev = i ? (xs[i - 1] + xs[i]) / 2 : minX, next = i < valid.length - 1 ? (xs[i] + xs[i + 1]) / 2 : maxX;
      return `<rect class="chart-hit-target" x="${x(prev)}" y="${top}" width="${Math.max(2, x(next) - x(prev))}" height="${innerHeight}" data-chart-index="${i}" tabindex="${i === 0 || i === valid.length - 1 ? '0' : '-1'}" aria-label="${escapeHTML(formatStamp(point.timestamp))}"/>`;
    }).join('');
    container.innerHTML = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="${chartId}-title"><title id="${chartId}-title">${escapeHTML(series.map((s) => s.label).join(', '))} over Chicago time. ${escapeHTML(unit)}. Hover or focus the chart for values.</title>${grid}${targetLine}${paths}${xTicks}${hits}</svg>`;
    const tooltipHandler = (event) => {
      const element = event.target.closest('[data-chart-index]'); if (!element) return;
      const point = valid[Number(element.dataset.chartIndex)];
      showTooltip(event, `${formatStamp(point.timestamp)} CT`, series.map((s) => [s.label, s.value(point) === null || s.value(point) === undefined ? 'Unavailable' : `${number(s.value(point), 2)}${unit}`]));
    };
    container.onpointermove = tooltipHandler; container.onpointerleave = hideTooltip; container.onfocusin = tooltipHandler; container.onfocusout = hideTooltip;
    container.onkeydown = (event) => {
      const element = event.target.closest('[data-chart-index]'); if (!element || !['ArrowLeft', 'ArrowRight', 'Escape'].includes(event.key)) return;
      if (event.key === 'Escape') { hideTooltip(); return; }
      event.preventDefault(); const index = Math.max(0, Math.min(valid.length - 1, Number(element.dataset.chartIndex) + (event.key === 'ArrowRight' ? 1 : -1)));
      container.querySelector(`[data-chart-index="${index}"]`)?.focus();
    };
  }
  function renderLegend(id, series) { $(id).innerHTML = series.map((s) => `<span class="legend-item"><span class="legend-line ${s.dashed ? 'dashed' : ''}" style="--legend-color:${s.color}"></span>${escapeHTML(s.label)}</span>`).join(''); }
  function scheduleSegments(strategy, config) {
    if (Array.isArray(strategy.schedule)) return strategy.schedule.map((segment) => ({ start: new Date(segment.start).getTime(), end: new Date(segment.end).getTime() })).filter((segment) => Number.isFinite(segment.start) && Number.isFinite(segment.end) && segment.end > segment.start);
    const timeline = strategy.timeline || [], result = []; let start = null, end = null;
    for (let i = 0; i < timeline.length; i++) {
      const point = timeline[i], time = new Date(point.interval_start || point.timestamp).getTime();
      const next = point.interval_start ? new Date(point.timestamp).getTime() : i < timeline.length - 1 ? new Date(timeline[i + 1].timestamp).getTime() : Math.min(time + 300000, new Date(config.departure).getTime());
      if (point.charging && next > time) { if (start === null) start = time; end = next; }
      else if (start !== null) { result.push({ start, end }); start = null; end = null; }
    }
    if (start !== null) result.push({ start, end });
    return result;
  }
  function renderSchedule(result) {
    const start = new Date(result.config.arrival).getTime(), end = new Date(result.config.departure).getTime(), span = end - start;
    $('schedule-chart').innerHTML = result.strategies.map((strategy, i) => {
      const color = modelColor(strategy.id, i), segments = scheduleSegments(strategy, result.config);
      return `<div class="schedule-row" style="--strategy-color:${color}"><span class="schedule-row-label" title="${escapeHTML(strategy.label)}${isOracle(strategy) ? ' — perfect foresight, not deployable' : ''}"><i class="strategy-dot"></i>${escapeHTML(strategy.label)}</span><div class="schedule-track" role="img" aria-label="${escapeHTML(strategy.label)}: ${segments.length ? segments.map((s) => `${formatTime(s.start)} to ${formatTime(s.end)}`).join(', ') : 'no charging'}">${segments.map((segment) => `<span class="schedule-block" style="left:${Math.max(0, (segment.start - start) / span * 100)}%;width:${Math.max(0, Math.min(end, segment.end) - Math.max(start, segment.start)) / span * 100}%" title="${escapeHTML(`${formatTime(segment.start)}–${formatTime(segment.end)} CT`)}"></span>`).join('')}</div><span class="schedule-cost">${money(strategy.cost)}</span></div>`;
    }).join('') + `<div class="schedule-axis">${[0, .25, .5, .75, 1].map((fraction) => `<span>${escapeHTML(formatTime(start + span * fraction))}</span>`).join('')}</div>`;
  }
  function renderResult(result) {
    if (!result?.strategies?.length) throw new Error('This experiment returned no strategies to display.');
    state.result = result;
    $('empty-state').classList.add('hidden'); $('result-content').classList.remove('hidden');
    $('result-title').textContent = `${formatStamp(result.config.arrival)} → ${formatTime(result.config.departure)}`;
    const deployable = result.strategies.filter((s) => !isOracle(s));
    const eligible = deployable.filter((s) => s.ready && !(result.config.price_policy === 'strict' && result.config.max_price_cents !== null && ['charge_now', 'charge-now'].includes(s.id)));
    const best = Object.prototype.hasOwnProperty.call(result.summary || {}, 'winner_id') ? eligible.find((s) => s.id === result.summary.winner_id) : eligible.slice().sort((a, b) => a.cost - b.cost)[0];
    const baseline = result.strategies.find((s) => ['charge_now', 'charge-now'].includes(s.id)) || result.strategies[0];
    const savings = best ? baseline.cost - best.cost : null, savingsPercent = best && baseline.cost > 0 ? savings / baseline.cost * 100 : null;
    const readyCount = deployable.filter((s) => s.ready).length;
    $('summary-cards').innerHTML = `<article class="metric-card highlight"><div class="metric-label">${best ? 'Lowest eligible cost' : 'No strategy ready'}<span aria-hidden="true">↗</span></div><div class="metric-value">${best ? money(best.cost) : '—'}</div><div class="metric-foot">${escapeHTML(best?.label || 'Review the energy shortfalls below')}</div></article><article class="metric-card"><div class="metric-label">Savings vs immediate<span aria-hidden="true">↘</span></div><div class="metric-value">${savings === null ? '—' : money(savings)}</div><div class="metric-foot">${savingsPercent === null ? 'Same energy goal, same actual prices' : `${number(savingsPercent)}% · ${money(baseline.cost)} immediate cost`}</div></article><article class="metric-card"><div class="metric-label">Ready at departure<span aria-hidden="true">◷</span></div><div class="metric-value">${readyCount}<small>/ ${deployable.length}</small></div><div class="metric-foot">Strategies reaching ${number(result.config.target_soc, 0)}% · oracle excluded</div></article>`;
    $('result-banner').innerHTML = `<span class="badge ${result.dataset?.is_synthetic ? 'synthetic' : 'real'}">${result.dataset?.is_synthetic ? 'SYNTHETIC DATA' : 'HISTORICAL DATA'}</span><strong>${escapeHTML(result.dataset?.name || 'Price dataset')}</strong><span>·</span><span>${result.config.price_policy === 'strict' ? 'Strict predicted price ceiling' : 'Readiness first'} · Hourly billing</span>`;
    if (result.id) { $('export-button').href = `/api/runs/${encodeURIComponent(result.id)}/export.csv`; $('export-button').classList.remove('hidden'); } else $('export-button').classList.add('hidden');
    const forecastIds = [...new Set((result.chart || []).flatMap((point) => Object.keys(point.forecasts || {})))];
    const priceSeries = [{ label: 'Actual billed price', color: '#344e3f', value: (p) => p.actual, width: 2.6 }, ...forecastIds.map((id, i) => ({ label: result.strategies.find((s) => s.id === id)?.label || state.models.find((m) => m.id === id)?.label || id, color: modelColor(id, i), dashed: true, value: (p) => p.forecasts?.[id] }))];
    if ((result.chart || []).some((p) => p.day_ahead !== null && p.day_ahead !== undefined) && !forecastIds.includes('day-ahead')) priceSeries.push({ label: 'Published day-ahead', color: '#c49b49', dashed: true, value: (p) => p.day_ahead });
    chart('price-chart', result.chart || [], priceSeries, { unit: ' ¢/kWh' }); renderLegend('price-legend', priceSeries);
    const times = [...new Set([result.config.arrival, ...result.strategies.flatMap((strategy) => (strategy.timeline || []).map((p) => p.timestamp))])].sort((a, b) => new Date(a) - new Date(b));
    const lookup = new Map(result.strategies.map((strategy) => [strategy.id, new Map([[result.config.arrival, result.config.initial_soc], ...(strategy.timeline || []).map((p) => [p.timestamp, p.soc])])]));
    const socPoints = times.map((timestamp) => ({ timestamp }));
    const socSeries = result.strategies.map((strategy, i) => ({ label: strategy.label, color: modelColor(strategy.id, i), dashed: isOracle(strategy), value: (p) => lookup.get(strategy.id).get(p.timestamp) }));
    chart('soc-chart', socPoints, socSeries, { unit: '%', fixedRange: [0, 100], target: result.config.target_soc }); renderLegend('soc-legend', socSeries);
    $('target-chart-badge').textContent = `TARGET ${number(result.config.target_soc, 0)}%`;
    renderSchedule(result); $('strategy-count').textContent = `${result.strategies.length} STRATEGIES`;
    $('comparison-body').innerHTML = result.strategies.map((strategy, i) => `<tr class="${isOracle(strategy) ? 'reference-row' : strategy.id === best?.id ? 'winner-row' : ''}"><td><span class="strategy-cell" style="--strategy-color:${modelColor(strategy.id, i)}"><i class="strategy-dot"></i>${escapeHTML(strategy.label)}</span>${isOracle(strategy) ? '<span class="small-reference">Perfect foresight · not deployable</span>' : strategy.id === best?.id ? '<span class="small-reference" style="color:#87a775">Lowest cost among ready strategies</span>' : ''}</td><td>${money(strategy.cost)}</td><td class="${strategy.savings > 0 ? 'saving-positive' : strategy.savings < 0 ? 'saving-negative' : ''}">${money(strategy.savings)}</td><td>${number(strategy.final_soc)}%</td><td><span class="ready-badge ${strategy.ready ? '' : 'missed'}" title="${strategy.ready ? 'Target reached by departure' : `${number(strategy.shortfall_kwh, 2)} kWh battery shortfall`}">${strategy.ready ? 'Ready ✓' : 'Shortfall'}</span></td><td>${strategy.mae === null || strategy.mae === undefined ? '—' : number(strategy.mae, 2)}</td><td>${number(strategy.switches, 0)}</td></tr>`).join('');
    const assumptions = Array.isArray(result.assumptions) ? result.assumptions : Object.values(result.assumptions || {}), warnings = result.warnings || [];
    $('result-notes').innerHTML = `<ul>${assumptions.map((note) => `<li>${escapeHTML(typeof note === 'string' ? note : JSON.stringify(note))}</li>`).join('')}<li>The oracle uses future actual prices and is a theoretical reference. It is excluded from the best deployable cost and readiness count.</li><li>The ceiling applies to predicted energy prices. Actual completed-hour billing prices may exceed it. Delivery charges are added separately.</li><li>Constant charging power is assumed; this does not model Tesla-specific taper, wake-up delays, or API failures.</li>${warnings.map((note) => `<li class="warning">${escapeHTML(note)}</li>`).join('')}</ul>`;
    savePreferences();
  }
  async function previewDataset(id) {
    const data = await api(`/api/datasets/${encodeURIComponent(id)}/preview`), dataset = data.dataset;
    $('data-preview-title').textContent = `${dataset.name} · hourly preview`; $('data-preview-panel').classList.remove('hidden');
    const series = [{ label: 'Actual hourly price', color: '#178879', value: (p) => p.price }];
    if (data.points.some((p) => p.day_ahead !== null && p.day_ahead !== undefined)) series.push({ label: 'Day-ahead price', color: '#c49b49', dashed: true, value: (p) => p.day_ahead });
    chart('data-preview-chart', data.points, series, { unit: ' ¢/kWh' });
    $('data-preview-notes').textContent = [...(dataset.is_synthetic ? ['SYNTHETIC DATA — generated for reproducible tests, not real market performance.'] : []), ...(dataset.warnings || [])].join(' ');
    $('data-preview-panel').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function setInlineStatus(id, message, error = false, loading = false) { $(id).classList.toggle('error', error); $(id).innerHTML = `${loading ? '<span class="spinner"></span>' : ''}${escapeHTML(message)}`; }

  $('arrival').step = $('departure').step = 300;
  window.addEventListener('hashchange', () => setPage(location.hash.slice(1))); setPage(location.hash.slice(1));
  document.querySelectorAll('[data-policy]').forEach((button) => button.addEventListener('click', () => { setPolicy(button.dataset.policy); savePreferences(); }));
  ['initial_soc', 'target_soc', 'battery_kwh', 'charger_kw', 'efficiency'].forEach((id) => $(id).addEventListener('input', updateEstimate));
  $('simulation-form').addEventListener('change', savePreferences);
  $('dataset-select').addEventListener('change', () => { updateDatasetCaption(); savePreferences(); });
  $('all-models-button').addEventListener('click', () => { document.querySelectorAll('.model-choice input:not(:disabled)').forEach((input) => input.checked = true); savePreferences(); });
  $('simulation-form').addEventListener('submit', async (event) => {
    event.preventDefault(); clearError();
    const button = $('run-button');
    try {
      const config = readRawForm(); config.arrival = chicagoISO(config.arrival); config.departure = chicagoISO(config.departure);
      if (new Date(config.departure) <= new Date(config.arrival)) throw new Error('Departure must be later than arrival.');
      if (config.target_soc < config.initial_soc) throw new Error('Target battery must be at least the current battery percentage.');
      const modelIds = [...document.querySelectorAll('.model-choice input:checked')].map((input) => input.value);
      if (!modelIds.length) throw new Error('Choose at least one available forecast model to compare.');
      savePreferences(); button.disabled = true; $('run-progress').classList.remove('hidden');
      $('run-progress').lastElementChild.textContent = 'Queued · preparing the simulation…';
      const job = await post('/api/runs', { dataset_id: $('dataset-select').value, config, model_ids: modelIds });
      let result = await pollJob(job.job_id, (status, seconds) => { $('run-progress').lastElementChild.textContent = `${status === 'queued' ? 'Queued' : 'Running comparison'} · ${seconds}s elapsed`; });
      if (result.run_id && !result.strategies) result = await api(`/api/runs/${encodeURIComponent(result.run_id)}`);
      renderResult(result); await refreshHistory(); toast('Experiment saved. Compare cost and readiness below.');
      if (window.innerWidth < 681) $('result-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) { apiError(error.message); $('global-error').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
    finally { button.disabled = false; $('run-progress').classList.add('hidden'); }
  });
  $('csv-file').addEventListener('change', async () => {
    const file = $('csv-file').files[0]; if (!file) return;
    if (file.size > 12 * 1024 * 1024) { setInlineStatus('import-status', 'This file is too large for a browser import. Use a smaller research window.', true); return; }
    try { $('csv-text').value = await file.text(); $('import-name').value = file.name.replace(/\.csv$/i, ''); setInlineStatus('import-status', `${file.name} loaded. Select Import dataset to validate it.`); } catch (error) { setInlineStatus('import-status', error.message, true); }
  });
  $('import-form').addEventListener('submit', async (event) => {
    event.preventDefault(); $('import-button').disabled = true; setInlineStatus('import-status', 'Validating and importing prices…', false, true);
    try { const data = await post('/api/datasets/import', { name: $('import-name').value.trim(), csv: $('csv-text').value }); await refreshBootstrap(); $('dataset-select').value = data.dataset.id; updateDatasetCaption(); savePreferences(); setInlineStatus('import-status', `${data.dataset.name} imported. ${number(data.dataset.hourly_count, 0)} complete hourly observations.`); toast('Dataset imported and selected in the simulator.'); }
    catch (error) { setInlineStatus('import-status', error.message, true); } finally { $('import-button').disabled = false; }
  });
  $('comed-form').addEventListener('submit', async (event) => {
    event.preventDefault(); $('comed-button').disabled = true; setInlineStatus('comed-status', 'Fetching ComEd prices. This can take a moment…', false, true);
    try { const data = await post('/api/datasets/comed', { start: $('comed-start').value, end: $('comed-end').value }); await refreshBootstrap(); $('dataset-select').value = data.dataset.id; updateDatasetCaption(); savePreferences(); setInlineStatus('comed-status', `${data.dataset.name} saved. ${number(data.dataset.hourly_count, 0)} complete billing hours available.`); toast('Historical prices added to the Data library.'); }
    catch (error) { setInlineStatus('comed-status', error.message, true); } finally { $('comed-button').disabled = false; }
  });
  $('data-library').addEventListener('click', async (event) => {
    const preview = event.target.closest('[data-preview]'), use = event.target.closest('[data-use-dataset]');
    if (preview) { preview.disabled = true; try { await previewDataset(preview.dataset.preview); } catch (error) { apiError(error.message); } finally { preview.disabled = false; } }
    if (use) { $('dataset-select').value = use.dataset.useDataset; updateDatasetCaption(); savePreferences(); location.hash = '#simulator'; toast('Dataset selected. Choose a session within its date range.'); }
  });
  $('close-preview').addEventListener('click', () => $('data-preview-panel').classList.add('hidden'));
  $('training-form').addEventListener('submit', async (event) => {
    event.preventDefault(); $('train-button').disabled = true;
    try {
      setInlineStatus('training-progress', 'Queued · preparing chronological training data…', false, true);
      const job = await post('/api/train', { dataset_id: $('training-dataset').value, model_id: $('training-model').value, train_fraction: Number($('train-fraction').value), config: {} });
      const result = await pollJob(job.job_id, (status, seconds) => setInlineStatus('training-progress', `${status === 'queued' ? 'Queued' : 'Training'} · ${seconds}s elapsed. Keep this page open.`, false, true));
      await refreshBootstrap();
      const metric = result.validation_mae ?? result.metrics?.validation_mae;
      setInlineStatus('training-progress', `Model trained and saved.${metric !== undefined ? ` Validation MAE: ${number(metric, 2)} ¢/kWh.` : ''}${result.trained_until ? ` Trained through ${formatStamp(result.trained_until)} CT. Run simulations after that time.` : ''}`);
      toast('Training artifact saved. The model bench is up to date.');
    } catch (error) { setInlineStatus('training-progress', error.message, true); }
    finally { $('train-button').disabled = false; }
  });
  $('refresh-artifacts').addEventListener('click', async () => { try { await refreshBootstrap(); toast('Model artifacts refreshed.'); } catch (error) { apiError(error.message); } });
  $('refresh-history').addEventListener('click', async () => { try { await refreshHistory(); toast('Experiment history refreshed.'); } catch (error) { apiError(error.message); } });
  $('history-list').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-open-run]'); if (!button) return; button.disabled = true;
    try { const result = await api(`/api/runs/${encodeURIComponent(button.dataset.openRun)}`); renderResult(result); applyConfig(result.config); if (state.datasets.some((d) => d.id === result.dataset.id)) $('dataset-select').value = result.dataset.id; updateDatasetCaption(); location.hash = '#simulator'; window.scrollTo({ top: 0, behavior: 'smooth' }); }
    catch (error) { apiError(error.message); } finally { button.disabled = false; }
  });
  window.addEventListener('resize', hideTooltip); window.addEventListener('scroll', hideTooltip, { passive: true });
  async function initialize() {
    try {
      const data = await api('/api/bootstrap'), preferences = loadPreferences();
      state.datasets = data.datasets || []; state.models = data.models || []; state.artifacts = data.artifacts || []; state.runs = data.recent_runs || [];
      state.selectedDataset = preferences.dataset_id || data.defaults?.dataset_id || state.datasets[0]?.id;
      state.selectedModels = new Set(preferences.model_ids || data.defaults?.model_ids || state.models.filter((model) => model.available).map((model) => model.id));
      renderAll();
      applyConfig({ arrival: '2026-10-14T18:00', departure: '2026-10-15T07:00', ...(data.defaults?.config || data.defaults || {}), ...(preferences.config || {}) });
      $('version-label').textContent = `Local environment · v${data.version || '1.0'}`;
      const arrivalDate = $('arrival').value.slice(0, 10), departureDate = $('departure').value.slice(0, 10);
      $('comed-start').value = arrivalDate; $('comed-end').value = departureDate;
      if (preferences.last_run_id) { try { const previous = await api(`/api/runs/${encodeURIComponent(preferences.last_run_id)}`); renderResult(previous); } catch { /* Deleted or moved history should not block the workspace. */ } }
    } catch (error) { apiError(`Could not connect to SPARC Lab. ${error.message} Start the local service and refresh this page.`); $('run-button').disabled = true; }
  }
  initialize();
})();
