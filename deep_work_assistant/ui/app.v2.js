const state = {
  data: null,
  cards: [],
  settings: null,
  selectedMinutes: 50,
  breathMinutes: 2,
  insightIndex: 0,
  timerInterval: null,
  breathInterval: null,
  refreshInterval: null,
  animationFrame: null,
  boardQuery: '',
  priorityFilter: 'all',
  tagFilter: 'all',
  sessionQuery: '',
  tutorialStep: 0,
  tutorialChecked: false,
  tutorialReturnFocus: null,
};

const TUTORIAL_VERSION = 1;
const TUTORIAL_STEPS = [
  {
    title: 'Two controls, two jobs',
    body: '<p><strong>Start Assistant</strong> watches Windows activity and sends hydration, stretch, meal, and optional posture reminders. <strong>Begin Focus</strong> starts the separate manual work-and-break timer. You can use either one or both.</p>',
  },
  {
    title: 'Check readiness',
    body: '<p>Open Settings & Diagnostics to confirm Windows activity capture, local storage, UI assets, and optional voice support are ready.</p><p class="truth-note">Voice is optional and uses an online TTS service. Core tracking and reminders do not require it.</p>',
  },
  {
    title: 'Plan and focus',
    body: '<p>Create a Work Board card if you want to organize the task. Link it to a focus timer when you want that timer’s completed work recorded against the card.</p><p class="truth-note">The automatic assistant and manual timer do not yet share one session identity. That connection is the next state-truth milestone.</p>',
  },
  {
    title: 'Respond and recover',
    body: '<p>Reminder time advances only while DWA sees human-active work. Agent-active and idle time pause the recovery clock. Confirm, skip, or let a reminder time out. Two accepted stretch skips trigger the 60-second primary-monitor overlay.</p>',
  },
  {
    title: 'Review the evidence',
    body: '<p>Session Intelligence shows completed sessions, applications, duration, human-versus-agent time, end reason, and recorded reminder outcomes. Work patterns and recovery intervals are calculated from local session history.</p>',
  },
  {
    title: 'What DWA learns—and what it does not',
    body: '<p>DWA learns app category, session length, flow style, and reminder-response patterns from completed sessions. It does <strong>not</strong> yet learn your personal posture baseline.</p><p class="truth-note">Optional vision is command-line opt-in. It uses brief local camera probes during human-active sessions, discards raw frames, and alerts after sustained generic threshold readings. It does not continuously record video.</p>',
  },
];

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const columns = [
  ['backlog', 'Backlog'],
  ['ready', 'Ready'],
  ['in_progress', 'In Progress'],
  ['review', 'Review'],
  ['done', 'Done'],
];

const fmtMinutes = value => {
  const minutes = Math.max(0, Math.round(Number(value) || 0));
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return hours ? `${hours}h ${remainder}m` : `${remainder}m`;
};
const fmtSeconds = value => fmtMinutes((Number(value) || 0) / 60);
const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
}[char]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {'Content-Type': 'application/json'},
    ...options,
    body: options.body && typeof options.body !== 'string' ? JSON.stringify(options.body) : options.body,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function toast(message, error = false) {
  const node = document.createElement('div');
  node.className = `toast${error ? ' error' : ''}`;
  node.textContent = message;
  $('#toastRegion').append(node);
  setTimeout(() => node.remove(), 3400);
}

function setConnection(ok, text) {
  $('#connectionText').textContent = text;
  $('#connectionDot').style.background = ok ? 'var(--accent2)' : 'var(--danger)';
}

async function loadDashboard(notify = false) {
  try {
    setConnection(false, 'Refreshing');
    state.data = await api('/api/dashboard');
    state.cards = state.data.board.cards || [];
    state.settings = state.data.settings || state.settings || {};
    applySettings();
    renderAll();
    setConnection(true, 'Local system online');
    if (notify) toast('Local data refreshed');
    await maybeOpenFirstRunTutorial();
  } catch (error) {
    setConnection(false, 'Connection failed');
    toast(error.message, true);
  }
}

function applySettings() {
  const settings = state.settings || {};
  document.documentElement.dataset.theme = settings.theme || 'cosmic';
  document.documentElement.dataset.motion = settings.motion || 'full';
  $('#themeSelect').value = settings.theme || 'cosmic';
  state.selectedMinutes = Number(settings.default_focus_minutes || state.selectedMinutes || 50);
  $('#hideDoneToggle').checked = Boolean(settings.hide_done_cards);
  selectDuration(state.selectedMinutes);
  scheduleRefresh();
  renderSettingsForm();
  startAmbientCanvas();
}

function scheduleRefresh() {
  clearInterval(state.refreshInterval);
  const seconds = Math.max(5, Number(state.settings?.auto_refresh_seconds || 15));
  state.refreshInterval = setInterval(() => {
    if (!document.hidden && !$('#cardDialog').open && !$('#sessionDialog').open) loadDashboard(false);
  }, seconds * 1000);
}

function renderAll() {
  const data = state.data;
  const weekly = data.weekly;
  const score = data.score;
  $('#weekFocus').textContent = fmtMinutes(weekly.total_focus_minutes);
  $('#weekSessions').textContent = `${weekly.total_sessions} sessions · avg ${fmtMinutes(weekly.average_session_minutes)}`;
  $('#weekFocusLine').style.width = `${Math.min(100, weekly.total_focus_minutes / 12)}%`;
  $('#productivityScore').textContent = score.score;
  $('#scoreComponents').innerHTML = Object.entries(score.components).map(([name, value]) =>
    `<i title="${esc(name.replaceAll('_', ' '))}: ${Math.round(value)}%" style="--value:${value}%"></i>`
  ).join('');
  $('#streakValue').textContent = `${data.streak.current_streak || 0} days`;
  $('#streakDetail').textContent = `Longest ${data.streak.longest_streak || 0} days · ${data.streak.daily_session_count || 0} today`;
  $('#hydrationPlan').textContent = `${data.plan.hydration_minutes}m`;
  $('#stretchPlan').textContent = `${data.plan.stretch_minutes}m`;
  $('#eatPlan').textContent = `${data.plan.eat_minutes}m`;
  const evidenceSessions = Number(data.profile.evidence_sessions || 0);
  $('#profileCategory').textContent = evidenceSessions < 3
    ? `calibrating · ${evidenceSessions}/3 sessions`
    : `${(data.profile.dominant_category || 'general').replaceAll('_', ' ')} · ${data.profile.confidence}`;
  $('#historyPath').textContent = data.paths.history;
  renderSystemStrip();
  renderTrend(data.trend || []);
  renderInsights(data.insights || []);
  renderBoardFilters();
  renderBoard();
  renderSessions();
  renderMix(data.activity_mix || {});
  renderRecommendations(score.recommendations || []);
  renderCategories(data.categories || {});
  renderPomodoro(data.pomodoro || {state: 'idle'});
  renderAssistant(data.assistant || {});
  renderCardSelect();
  renderDiagnostics(data.diagnostics || {});
  renderLogs(data.assistant?.recent_log || []);
  renderHelp();
}

function renderSystemStrip() {
  const data = state.data;
  const evidence = Number(data.profile.evidence_sessions || 0);
  $('#systemProfile').textContent = evidence < 3
    ? `Learning · ${evidence}/3 sessions`
    : `${String(data.profile.flow_style || data.profile.dominant_category).replaceAll('_', ' ')} · ${data.profile.confidence}`;
  const best = data.best_hours?.[0];
  $('#bestWindow').textContent = best ? `${String(best.hour).padStart(2, '0')}:00 · ${fmtMinutes(best.minutes)}` : 'No data';
  const human = Math.round((data.activity_mix?.human_ratio || 0) * 100);
  $('#systemMix').textContent = data.activity_mix?.human_seconds || data.activity_mix?.agent_seconds ? `${human}% human` : 'No data';
  $('#systemAssistant').textContent = data.assistant?.running ? `Running · PID ${data.assistant.pid}` : 'Stopped';
}

function renderTrend(points) {
  const canvas = $('#trendChart');
  const context = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  const ratio = devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * ratio);
  canvas.height = 180 * ratio;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = rect.width;
  const height = 180;
  const padding = 18;
  const values = points.map(point => point.focus_minutes);
  const max = Math.max(60, ...values);
  const styles = getComputedStyle(document.documentElement);
  const accent = styles.getPropertyValue('--accent2').trim();
  const secondary = styles.getPropertyValue('--accent').trim();
  context.clearRect(0, 0, width, height);
  context.strokeStyle = 'rgba(255,255,255,.1)';
  for (let i = 0; i < 4; i += 1) {
    const y = padding + ((height - padding * 2) * i / 3);
    context.beginPath(); context.moveTo(padding, y); context.lineTo(width - padding, y); context.stroke();
  }
  const plotted = points.map((point, index) => ({
    x: padding + ((width - padding * 2) * index / Math.max(1, points.length - 1)),
    y: height - padding - ((point.focus_minutes / max) * (height - padding * 2)),
  }));
  if (plotted.length) {
    const gradient = context.createLinearGradient(0, 0, width, 0);
    gradient.addColorStop(0, secondary);
    gradient.addColorStop(1, accent);
    context.beginPath();
    plotted.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
    context.strokeStyle = gradient;
    context.lineWidth = 2.5;
    context.stroke();
    plotted.forEach(point => {
      context.beginPath(); context.arc(point.x, point.y, 3, 0, Math.PI * 2); context.fillStyle = accent; context.fill();
    });
  }
  $('#trendTotal').textContent = `${values.reduce((sum, value) => sum + value, 0)} minutes`;
}

function renderInsights(list) {
  state.insightIndex = Math.min(state.insightIndex, Math.max(0, list.length - 1));
  $('#insightText').textContent = list[state.insightIndex] || 'Complete a focus session to generate local intelligence.';
}

function filteredCards() {
  const query = state.boardQuery.trim().toLowerCase();
  return state.cards.filter(card => {
    if ($('#hideDoneToggle').checked && card.column === 'done') return false;
    if (state.priorityFilter !== 'all' && String(card.priority) !== state.priorityFilter) return false;
    if (state.tagFilter !== 'all' && !(card.tags || []).includes(state.tagFilter)) return false;
    if (!query) return true;
    return [card.title, card.description, ...(card.tags || [])].join(' ').toLowerCase().includes(query);
  });
}

function renderBoardFilters() {
  const tags = [...new Set(state.cards.flatMap(card => card.tags || []))].sort((a, b) => a.localeCompare(b));
  const select = $('#tagFilter');
  const current = state.tagFilter;
  select.innerHTML = '<option value="all">All tags</option>' + tags.map(tag => `<option value="${esc(tag)}">${esc(tag)}</option>`).join('');
  select.value = tags.includes(current) ? current : 'all';
  state.tagFilter = select.value;
}

function cardTemplate(card) {
  const priority = ['Normal', 'High', 'Urgent'][card.priority] || 'Normal';
  const appLink = card.linked_app_pattern ? `<span class="context-chip">${esc(card.linked_app_pattern)}</span>` : '';
  return `<article class="work-card priority-${card.priority}" draggable="true" data-id="${card.card_id}" tabindex="0">
    <div class="card-title-row"><h4>${esc(card.title)}</h4><span class="priority-mark">${priority}</span></div>
    ${card.description ? `<p>${esc(card.description)}</p>` : ''}
    <div>${(card.tags || []).slice(0, 4).map(tag => `<span class="tag">${esc(tag)}</span>`).join('')}${appLink}</div>
    <div class="card-meta"><span>${Math.round(card.session_time_seconds / 60)}m logged</span><button class="mini-action" data-log-id="${card.card_id}" type="button">+ time</button></div>
  </article>`;
}

function renderBoard() {
  const cards = filteredCards();
  $('#boardSummary').innerHTML = `
    <span class="summary-chip">${cards.length} shown</span>
    <span class="summary-chip">${state.cards.length} total cards</span>
    <span class="summary-chip">${fmtSeconds(state.data.board.total_session_seconds)} logged</span>`;
  $('#kanbanBoard').innerHTML = columns
    .filter(([id]) => !($('#hideDoneToggle').checked && id === 'done'))
    .map(([id, label]) => `<section class="kanban-column" data-column="${id}">
      <div class="column-head"><h3>${label}</h3><span>${cards.filter(card => card.column === id).length}</span></div>
      <div class="card-list">${cards.filter(card => card.column === id).map(cardTemplate).join('')}</div>
    </section>`).join('');

  $$('.work-card').forEach(card => {
    card.ondragstart = event => event.dataTransfer.setData('text/plain', card.dataset.id);
    card.onclick = event => {
      if (!event.target.closest('[data-log-id]')) openCard(card.dataset.id);
    };
    card.onkeydown = event => {
      if (event.key === 'Enter') openCard(card.dataset.id);
    };
  });
  $$('[data-log-id]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      quickLogTime(button.dataset.logId);
    };
  });
  $$('.kanban-column').forEach(column => {
    column.ondragover = event => { event.preventDefault(); column.classList.add('drag-target'); };
    column.ondragleave = () => column.classList.remove('drag-target');
    column.ondrop = async event => {
      event.preventDefault();
      column.classList.remove('drag-target');
      try {
        await api(`/api/cards/${event.dataTransfer.getData('text/plain')}/move`, {method: 'POST', body: {column: column.dataset.column}});
        await loadDashboard();
        toast('Card moved');
      } catch (error) { toast(error.message, true); }
    };
  });
}

async function quickLogTime(cardId) {
  const raw = prompt('Minutes to add to this card:', '25');
  if (raw === null) return;
  const minutes = Number(raw);
  if (!Number.isInteger(minutes) || minutes <= 0) return toast('Enter a whole number greater than zero', true);
  try {
    await api(`/api/cards/${cardId}/log`, {method: 'POST', body: {minutes}});
    await loadDashboard();
    toast(`${minutes} minutes logged`);
  } catch (error) { toast(error.message, true); }
}

function renderSessions() {
  const query = state.sessionQuery.trim().toLowerCase();
  const sessions = (state.data.recent_sessions || []).filter(session =>
    !query || String(session.primary_app || '').toLowerCase().includes(query)
  );
  const summary = state.data.session_summary || {};
  $('#sessionSummary').innerHTML = `
    <span class="summary-chip">${summary.visible_count || 0} recent records</span>
    <span class="summary-chip">${fmtSeconds(summary.visible_seconds || 0)} total</span>
    <span class="summary-chip">${fmtSeconds(summary.average_seconds || 0)} average</span>`;
  $('#sessionRows').innerHTML = sessions.length ? sessions.map((session, index) => {
    const date = new Date(session.started_at);
    const reminders = (session.reminder_outcomes || []).filter(item => item.outcome !== 'not_sent');
    return `<tr data-session-index="${index}" tabindex="0">
      <td>${date.toLocaleDateString([], {month: 'short', day: 'numeric'})}<br><small>${date.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}</small></td>
      <td>${esc(session.primary_app || 'Unknown')}</td>
      <td>${fmtSeconds(session.duration_seconds)}</td>
      <td>${esc(String(session.ended_reason || 'unknown').replaceAll('-', ' '))}</td>
      <td>${reminders.length}</td>
    </tr>`;
  }).join('') : '<tr><td colspan="5">No matching completed sessions.</td></tr>';
  $$('[data-session-index]').forEach(row => {
    row.onclick = () => openSession(sessions[Number(row.dataset.sessionIndex)]);
    row.onkeydown = event => { if (event.key === 'Enter') openSession(sessions[Number(row.dataset.sessionIndex)]); };
  });
}

function openSession(session) {
  const reminders = session.reminder_outcomes || [];
  $('#sessionDialogTitle').textContent = session.primary_app || 'Session details';
  $('#sessionDetail').innerHTML = `
    <div class="detail-grid">
      <div><span>Started</span><strong>${new Date(session.started_at).toLocaleString()}</strong></div>
      <div><span>Duration</span><strong>${fmtSeconds(session.duration_seconds)}</strong></div>
      <div><span>Human active</span><strong>${fmtSeconds(session.human_active_seconds || 0)}</strong></div>
      <div><span>Agent active</span><strong>${fmtSeconds(session.agent_active_seconds || 0)}</strong></div>
      <div><span>Average idle</span><strong>${fmtSeconds(session.average_idle_seconds || 0)}</strong></div>
      <div><span>Ended reason</span><strong>${esc(String(session.ended_reason || 'unknown').replaceAll('-', ' '))}</strong></div>
    </div>
    <h3>Reminder outcomes</h3>
    <div class="reminder-ledger">${reminders.length ? reminders.map(item => `<div><span>${esc(item.stage || 'reminder')}</span><strong>${esc(item.outcome || 'unknown')}</strong><small>${item.resolved_at ? new Date(item.resolved_at).toLocaleTimeString() : ''}</small></div>`).join('') : '<p>No reminder outcomes recorded.</p>'}</div>`;
  $('#sessionDialog').showModal();
}

function renderMix(mix) {
  const human = Math.round((mix.human_ratio || 0) * 100);
  $('#humanRatio').textContent = `${human}%`;
  $('#humanTime').textContent = fmtSeconds(mix.human_seconds);
  $('#agentTime').textContent = fmtSeconds(mix.agent_seconds);
  $('#mixRing').style.setProperty('--human', `${human}%`);
}

function renderRecommendations(items) {
  $('#recommendations').innerHTML = items.length
    ? items.map(item => `<div class="recommendation">${esc(item)}</div>`).join('')
    : '<div class="recommendation">Complete sessions to generate recommendations.</div>';
}

function renderCategories(categories) {
  const entries = Object.entries(categories);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  $('#categoryBars').innerHTML = entries.length ? entries.map(([name, value]) => `<div class="category-row">
    <span>${esc(name)}</span><div class="category-track"><i style="--width:${value / max * 100}%"></i></div><b>${fmtMinutes(value)}</b>
  </div>`).join('') : '<div class="recommendation">No category history yet.</div>';
}

function renderCardSelect() {
  const select = $('#focusCardSelect');
  const current = select.value;
  select.innerHTML = '<option value="">No linked card</option>' + state.cards.filter(card => card.column !== 'done').map(card => `<option value="${card.card_id}">${esc(card.title)}</option>`).join('');
  select.value = state.data.pomodoro?.active_card_id || current;
}

function renderPomodoro(pomodoro) {
  clearInterval(state.timerInterval);
  const active = pomodoro && pomodoro.state && pomodoro.state !== 'idle';
  $('#focusLiveTag').textContent = active ? pomodoro.state.replace('_', ' ').toUpperCase() : 'IDLE';
  $('#focusStateLabel').textContent = active ? (pomodoro.state === 'working' ? 'Deep work in progress' : 'Recovery phase') : 'Ready for a session';
  $('#startFocusButton').disabled = active;
  ['pauseFocusButton', 'nextFocusButton', 'stopFocusButton'].forEach(id => $(`#${id}`).disabled = !active);
  if (!active) {
    $('#timerValue').textContent = `${state.selectedMinutes}:00`;
    $('#timerPhase').textContent = 'READY';
    $('#orbProgress').style.strokeDashoffset = '0';
    return;
  }
  let remaining = Math.max(0, Math.round((pomodoro.remaining_minutes || 0) * 60));
  const total = Math.max(1, (pomodoro.phase_duration_minutes || 25) * 60);
  const paused = Boolean(pomodoro.is_paused);
  const paint = () => {
    $('#timerValue').textContent = `${String(Math.floor(remaining / 60)).padStart(2, '0')}:${String(remaining % 60).padStart(2, '0')}`;
    $('#timerPhase').textContent = paused ? 'PAUSED' : pomodoro.state.replace('_', ' ').toUpperCase();
    $('#orbProgress').style.strokeDashoffset = String(590.6 * (remaining / total));
  };
  paint();
  if (!paused) state.timerInterval = setInterval(() => {
    remaining = Math.max(0, remaining - 1);
    paint();
    if (!remaining) { clearInterval(state.timerInterval); loadDashboard(); }
  }, 1000);
}

function renderAssistant(assistant) {
  $('#assistantButton').textContent = assistant.running ? 'Stop Assistant' : 'Start Assistant';
  $('#assistantButton').classList.toggle('danger', Boolean(assistant.running));
}

function openCard(id = null) {
  const card = id ? state.cards.find(item => item.card_id === id) : null;
  $('#cardDialogTitle').textContent = card ? 'Edit card' : 'New card';
  $('#cardId').value = card?.card_id || '';
  $('#cardTitle').value = card?.title || '';
  $('#cardDescription').value = card?.description || '';
  $('#cardColumn').value = card?.column || 'backlog';
  $('#cardPriority').value = String(card?.priority || 0);
  $('#cardTags').value = (card?.tags || []).join(', ');
  $('#cardApp').value = card?.linked_app_pattern || '';
  $('#cardWindow').value = card?.linked_window_pattern || '';
  $('#cardLogMinutes').value = '0';
  $('#deleteCardButton').hidden = !card;
  $('#cardDialog').showModal();
  setTimeout(() => $('#cardTitle').focus(), 50);
}

async function saveCard(event) {
  event.preventDefault();
  const id = $('#cardId').value;
  const payload = {
    title: $('#cardTitle').value,
    description: $('#cardDescription').value,
    column: $('#cardColumn').value,
    priority: Number($('#cardPriority').value),
    tags: $('#cardTags').value,
    linked_app_pattern: $('#cardApp').value,
    linked_window_pattern: $('#cardWindow').value,
  };
  const logMinutes = Number($('#cardLogMinutes').value || 0);
  try {
    if (id) {
      const old = state.cards.find(card => card.card_id === id);
      await api(`/api/cards/${id}`, {method: 'PATCH', body: payload});
      if (old.column !== payload.column) await api(`/api/cards/${id}/move`, {method: 'POST', body: {column: payload.column}});
      if (logMinutes > 0) await api(`/api/cards/${id}/log`, {method: 'POST', body: {minutes: logMinutes}});
    } else {
      const created = await api('/api/cards', {method: 'POST', body: payload});
      if (logMinutes > 0) await api(`/api/cards/${created.card_id}/log`, {method: 'POST', body: {minutes: logMinutes}});
    }
    $('#cardDialog').close();
    await loadDashboard();
    toast(id ? 'Card updated' : 'Card created');
  } catch (error) { toast(error.message, true); }
}

function selectDuration(minutes) {
  state.selectedMinutes = Number(minutes) || 50;
  $$('.duration-pill[data-minutes]').forEach(button => button.classList.toggle('active', Number(button.dataset.minutes) === state.selectedMinutes));
  if (!state.data?.pomodoro || state.data.pomodoro.state === 'idle') renderPomodoro({state: 'idle'});
}

async function startFocus() {
  try {
    const settings = state.settings || {};
    state.data.pomodoro = await api('/api/pomodoro/start', {method: 'POST', body: {
      work_minutes: state.selectedMinutes,
      short_break_minutes: Number(settings.short_break_minutes || 5),
      long_break_minutes: Number(settings.long_break_minutes || 15),
      pomodoros_before_long: Number(settings.pomodoros_before_long || 4),
      card_id: $('#focusCardSelect').value,
    }});
    renderPomodoro(state.data.pomodoro);
    toast('Focus session started');
  } catch (error) { toast(error.message, true); }
}

async function pomo(action) {
  try {
    const result = await api(`/api/pomodoro/${action}`, {method: 'POST', body: {}});
    if (action === 'stop') await loadDashboard();
    else { state.data.pomodoro = result; renderPomodoro(result); }
  } catch (error) { toast(error.message, true); }
}

function startBreathing() {
  clearInterval(state.breathInterval);
  const end = Date.now() + state.breathMinutes * 60000;
  const phases = [['Inhale', 'active'], ['Hold', 'active'], ['Exhale', 'exhale'], ['Hold', 'exhale']];
  let phaseIndex = 0;
  let count = 4;
  $('#breathButton').disabled = true;
  $('#breathStopButton').disabled = false;
  const tick = () => {
    if (Date.now() >= end) { stopBreathing(); toast(`${state.breathMinutes}-minute reset complete`); return; }
    $('#breathPhase').textContent = phases[phaseIndex][0];
    $('#breathCount').textContent = count;
    $('#breathVisual').className = `breath-visual ${phases[phaseIndex][1]}`;
    count -= 1;
    if (count === 0) { phaseIndex = (phaseIndex + 1) % 4; count = 4; }
  };
  tick();
  state.breathInterval = setInterval(tick, 1000);
}

function stopBreathing() {
  clearInterval(state.breathInterval);
  $('#breathVisual').className = 'breath-visual';
  $('#breathPhase').textContent = 'Ready';
  $('#breathCount').textContent = '4';
  $('#breathButton').disabled = false;
  $('#breathStopButton').disabled = true;
}

function renderHelp() {
  $('#tutorialGuide').innerHTML = TUTORIAL_STEPS.map((step, index) => `
    <article class="tutorial-card">
      <span>STEP ${index + 1}</span>
      <h3>${esc(step.title)}</h3>
      ${step.body}
    </article>`).join('');
  updateSupportPreview();
}

function renderTutorialStep() {
  const step = TUTORIAL_STEPS[state.tutorialStep];
  $('#tutorialProgress').textContent = `STEP ${state.tutorialStep + 1} OF ${TUTORIAL_STEPS.length}`;
  $('#tutorialTitle').textContent = step.title;
  $('#tutorialBody').innerHTML = step.body;
  $('#tutorialBackButton').disabled = state.tutorialStep === 0;
  $('#tutorialNextButton').textContent = state.tutorialStep === TUTORIAL_STEPS.length - 1 ? 'Finish' : 'Next';
}

async function persistTutorial(update) {
  try {
    state.settings = await api('/api/settings', {method: 'PATCH', body: update});
  } catch (error) {
    toast(`Tutorial preference was not saved: ${error.message}`, true);
  }
}

async function openTutorial(step = 0, trigger = document.activeElement) {
  state.tutorialStep = Math.max(0, Math.min(TUTORIAL_STEPS.length - 1, Number(step) || 0));
  state.tutorialReturnFocus = trigger instanceof HTMLElement ? trigger : null;
  renderTutorialStep();
  if (!$('#tutorialDialog').open) $('#tutorialDialog').showModal();
  await persistTutorial({tutorial_seen_version: TUTORIAL_VERSION});
  setTimeout(() => $('#tutorialTitle').focus(), 20);
}

function closeTutorial() {
  if ($('#tutorialDialog').open) $('#tutorialDialog').close();
  state.tutorialReturnFocus?.focus();
}

async function maybeOpenFirstRunTutorial() {
  if (state.tutorialChecked || !state.settings) return;
  state.tutorialChecked = true;
  if (Number(state.settings.tutorial_seen_version || 0) < TUTORIAL_VERSION) {
    await openTutorial(0, $('#assistantButton'));
  }
}

function sanitizedDiagnostics() {
  const value = state.data?.diagnostics || {};
  return {
    version: value.version || 'unknown',
    status: value.status || 'unknown',
    platform: value.platform || 'unknown',
    python: value.python || 'unknown',
    windows_activity_capture: Boolean(value.windows_activity_capture),
    voice_available: Boolean(value.voice_available),
    vision_installed: Boolean(value.vision?.installed),
    posture_personalization: value.learning?.posture_personalization || 'not implemented',
    assistant_running: Boolean(value.assistant?.running),
  };
}

function buildSupportNote() {
  const sections = [
    `# Deep Work Assistant ${$('#supportCategory').value}`,
    `\n## What happened?\n${$('#supportHappened').value.trim() || '(not provided)'}`,
    `\n## What did you expect?\n${$('#supportExpected').value.trim() || '(not provided)'}`,
    `\n## How can it be reproduced?\n${$('#supportReproduce').value.trim() || '(not provided)'}`,
  ];
  if ($('#supportDiagnostics').checked) {
    sections.push(`\n## Sanitized diagnostics\n\`\`\`json\n${JSON.stringify(sanitizedDiagnostics(), null, 2)}\n\`\`\``);
  }
  sections.push('\nNothing was sent automatically; this note was reviewed before posting.');
  return sections.join('\n');
}

function updateSupportPreview() {
  if ($('#supportPreview')) $('#supportPreview').value = buildSupportNote();
}

async function copySupportNote() {
  updateSupportPreview();
  const preview = $('#supportPreview');
  try {
    await navigator.clipboard.writeText(preview.value);
    toast('Support note copied. Review it before posting.');
  } catch (_) {
    preview.removeAttribute('readonly');
    preview.focus();
    preview.select();
    const copied = document.execCommand('copy');
    preview.setAttribute('readonly', '');
    toast(copied ? 'Support note copied' : 'Clipboard blocked. The note is selected for manual copy.', !copied);
  }
}

function renderSettingsForm() {
  const settings = state.settings || {};
  $('#settingFocus').value = settings.default_focus_minutes ?? 50;
  $('#settingRefresh').value = settings.auto_refresh_seconds ?? 15;
  $('#settingShortBreak').value = settings.short_break_minutes ?? 5;
  $('#settingLongBreak').value = settings.long_break_minutes ?? 15;
  $('#settingPoll').value = settings.poll_interval ?? 15;
  $('#settingResponse').value = settings.response_window ?? 10;
  $('#settingObsidian').value = settings.obsidian_vault ?? '';
  $('#settingVoice').checked = Boolean(settings.voice);
  $('#settingPreannounce').checked = Boolean(settings.voice_pre_announce);
  $('#settingAutoStart').checked = Boolean(settings.auto_start_assistant);
  $('#settingReducedMotion').checked = settings.motion === 'reduced';
}

async function saveSettings() {
  const payload = {
    theme: $('#themeSelect').value,
    motion: $('#settingReducedMotion').checked ? 'reduced' : 'full',
    auto_refresh_seconds: Number($('#settingRefresh').value),
    default_focus_minutes: Number($('#settingFocus').value),
    short_break_minutes: Number($('#settingShortBreak').value),
    long_break_minutes: Number($('#settingLongBreak').value),
    poll_interval: Number($('#settingPoll').value),
    response_window: Number($('#settingResponse').value),
    obsidian_vault: $('#settingObsidian').value,
    voice: $('#settingVoice').checked,
    voice_pre_announce: $('#settingPreannounce').checked,
    auto_start_assistant: $('#settingAutoStart').checked,
    hide_done_cards: $('#hideDoneToggle').checked,
  };
  try {
    state.settings = await api('/api/settings', {method: 'PATCH', body: payload});
    applySettings();
    renderBoard();
    toast('Settings saved locally');
  } catch (error) { toast(error.message, true); }
}

function renderDiagnostics(diagnostics) {
  const paths = diagnostics.paths || {};
  const cards = [
    ['DWA build', diagnostics.version || 'Unknown', 'good'],
    ['Platform', diagnostics.platform || 'Unknown', diagnostics.windows_activity_capture ? 'good' : 'warn'],
    ['Python', diagnostics.python || 'Unknown', 'good'],
    ['Windows activity capture', diagnostics.windows_activity_capture ? 'Ready' : 'Unavailable here', diagnostics.windows_activity_capture ? 'good' : 'warn'],
    ['Voice TTS', diagnostics.voice_available ? 'Available' : 'edge-tts not installed', diagnostics.voice_available ? 'good' : 'warn'],
    ['Vision', diagnostics.vision?.installed ? 'Installed · opt-in' : 'Not installed', diagnostics.vision?.installed ? 'warn' : 'warn'],
    ['Posture baseline', diagnostics.vision?.personal_posture_baseline ? 'Calibrated' : 'Not learned', diagnostics.vision?.personal_posture_baseline ? 'good' : 'warn'],
    ['Stored sessions', String(diagnostics.counts?.sessions || 0), 'good'],
    ['Kanban cards', String(diagnostics.counts?.cards || 0), 'good'],
  ];
  const pathRows = Object.entries(paths).map(([name, item]) => `<div class="path-check"><span>${esc(name.replaceAll('_', ' '))}</span><strong class="${item.writable ? 'good' : 'bad'}">${item.writable ? 'Writable' : 'Blocked'}</strong><small title="${esc(item.path)}">${esc(item.path)}</small></div>`).join('');
  $('#diagnosticsGrid').innerHTML = cards.map(([label, value, status]) => `<div class="diagnostic-card"><span>${label}</span><strong class="${status}">${esc(value)}</strong></div>`).join('') + `<div class="path-list">${pathRows}</div>`;
}

function renderLogs(lines) {
  $('#assistantLog').textContent = lines?.length ? lines.join('\n') : 'No UI-managed assistant output yet.';
  $('#assistantLog').scrollTop = $('#assistantLog').scrollHeight;
}

async function refreshDiagnostics() {
  try {
    const diagnostics = await api('/api/diagnostics');
    renderDiagnostics(diagnostics);
    toast('Diagnostics refreshed');
  } catch (error) { toast(error.message, true); }
}

async function refreshLogs() {
  try {
    const result = await api('/api/assistant/logs');
    renderLogs(result.lines || []);
  } catch (error) { toast(error.message, true); }
}

function startAmbientCanvas() {
  cancelAnimationFrame(state.animationFrame);
  const canvas = $('#ambientCanvas');
  const context = canvas.getContext('2d');
  const reduced = document.documentElement.dataset.motion === 'reduced' || matchMedia('(prefers-reduced-motion: reduce)').matches;
  let particles = [];
  const resize = () => {
    const ratio = Math.min(2, devicePixelRatio || 1);
    canvas.width = innerWidth * ratio;
    canvas.height = innerHeight * ratio;
    canvas.style.width = `${innerWidth}px`;
    canvas.style.height = `${innerHeight}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    particles = Array.from({length: reduced ? 12 : Math.min(70, Math.floor(innerWidth / 20))}, () => ({
      x: Math.random() * innerWidth,
      y: Math.random() * innerHeight,
      r: Math.random() * 1.8 + .4,
      vx: (Math.random() - .5) * .18,
      vy: (Math.random() - .5) * .18,
      phase: Math.random() * Math.PI * 2,
    }));
  };
  resize();
  window.onresize = () => { resize(); if (state.data) renderTrend(state.data.trend || []); };
  const frame = time => {
    context.clearRect(0, 0, innerWidth, innerHeight);
    const styles = getComputedStyle(document.documentElement);
    const accent = styles.getPropertyValue('--accent').trim();
    const accent2 = styles.getPropertyValue('--accent2').trim();
    particles.forEach((particle, index) => {
      if (!reduced) {
        particle.x += particle.vx;
        particle.y += particle.vy;
        if (particle.x < -10) particle.x = innerWidth + 10;
        if (particle.x > innerWidth + 10) particle.x = -10;
        if (particle.y < -10) particle.y = innerHeight + 10;
        if (particle.y > innerHeight + 10) particle.y = -10;
      }
      context.globalAlpha = .16 + Math.sin(time / 1100 + particle.phase) * .07;
      context.fillStyle = index % 2 ? accent : accent2;
      context.beginPath(); context.arc(particle.x, particle.y, particle.r, 0, Math.PI * 2); context.fill();
    });
    context.globalAlpha = 1;
    state.animationFrame = requestAnimationFrame(frame);
  };
  state.animationFrame = requestAnimationFrame(frame);
}

function showPage(target, label = null) {
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.target === target));
  $$('.page').forEach(page => page.classList.toggle('active', page.id === target));
  $('#pageTitle').textContent = label || $(`.nav-item[data-target="${target}"]`)?.textContent.trim() || target;
  document.body.dataset.page = target;
  location.hash = target;
}

function bindEvents() {
  $$('.nav-item').forEach(button => button.onclick = () => showPage(button.dataset.target, button.textContent.trim()));
  $('#refreshButton').onclick = () => loadDashboard(true);
  $('#helpButton').onclick = () => openTutorial(0, $('#helpButton'));
  $('#focusHelpButton').onclick = () => openTutorial(0, $('#focusHelpButton'));
  $('#reopenTutorialButton').onclick = () => openTutorial(0, $('#reopenTutorialButton'));
  $('#newCardButton').onclick = () => openCard();
  $('#cardForm').onsubmit = saveCard;
  $('#deleteCardButton').onclick = async () => {
    const id = $('#cardId').value;
    if (id && confirm('Delete this card?')) {
      try {
        await api(`/api/cards/${id}`, {method: 'DELETE'});
        $('#cardDialog').close();
        await loadDashboard();
        toast('Card deleted');
      } catch (error) { toast(error.message, true); }
    }
  };
  $$('.duration-pill[data-minutes]').forEach(button => button.onclick = () => selectDuration(button.dataset.minutes));
  $$('.duration-pill[data-breath-minutes]').forEach(button => button.onclick = () => {
    $$('.duration-pill[data-breath-minutes]').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    state.breathMinutes = Number(button.dataset.breathMinutes);
  });
  $('#startFocusButton').onclick = startFocus;
  $('#pauseFocusButton').onclick = () => pomo(state.data.pomodoro.is_paused ? 'resume' : 'pause');
  $('#nextFocusButton').onclick = () => pomo('next');
  $('#stopFocusButton').onclick = () => pomo('stop');
  $('#nextInsightButton').onclick = () => {
    const list = state.data.insights || [];
    state.insightIndex = list.length ? (state.insightIndex + 1) % list.length : 0;
    renderInsights(list);
  };
  $('#assistantButton').onclick = async () => {
    try {
      const running = state.data.assistant.running;
      await api(`/api/assistant/${running ? 'stop' : 'start'}`, {method: 'POST', body: state.settings || {}});
      await new Promise(resolve => setTimeout(resolve, 350));
      await loadDashboard();
      if (running) toast('Assistant stopped');
      else if (state.data.assistant.running) toast('Assistant started');
      else toast('Assistant could not stay running. Check Diagnostics and the process log.', true);
    } catch (error) { toast(error.message, true); }
  };
  $('#breathButton').onclick = startBreathing;
  $('#breathStopButton').onclick = stopBreathing;
  $('#themeSelect').onchange = event => {
    document.documentElement.dataset.theme = event.target.value;
    state.settings = {...(state.settings || {}), theme: event.target.value};
    startAmbientCanvas();
    if (state.data) renderTrend(state.data.trend || []);
  };
  $('#boardSearch').oninput = event => { state.boardQuery = event.target.value; renderBoard(); };
  $('#priorityFilter').onchange = event => { state.priorityFilter = event.target.value; renderBoard(); };
  $('#tagFilter').onchange = event => { state.tagFilter = event.target.value; renderBoard(); };
  $('#hideDoneToggle').onchange = () => renderBoard();
  $('#sessionSearch').oninput = event => { state.sessionQuery = event.target.value; renderSessions(); };
  $('#closeSessionDialog').onclick = () => $('#sessionDialog').close();
  $('#saveSettingsButton').onclick = saveSettings;
  $('#settingsForm').onsubmit = event => { event.preventDefault(); saveSettings(); };
  $('#refreshDiagnosticsButton').onclick = refreshDiagnostics;
  $('#refreshLogsButton').onclick = refreshLogs;
  $('#closeTutorialButton').onclick = closeTutorial;
  $('#tutorialSkipButton').onclick = closeTutorial;
  $('#tutorialBackButton').onclick = () => {
    state.tutorialStep = Math.max(0, state.tutorialStep - 1);
    renderTutorialStep();
    $('#tutorialTitle').focus();
  };
  $('#tutorialNextButton').onclick = async () => {
    if (state.tutorialStep === TUTORIAL_STEPS.length - 1) {
      await persistTutorial({
        tutorial_seen_version: TUTORIAL_VERSION,
        tutorial_completed_version: TUTORIAL_VERSION,
      });
      closeTutorial();
      toast('Tutorial complete. Help & Feedback stays available.');
      return;
    }
    state.tutorialStep += 1;
    renderTutorialStep();
    $('#tutorialTitle').focus();
  };
  $('#tutorialDialog').addEventListener('close', () => state.tutorialReturnFocus?.focus());
  ['supportCategory', 'supportHappened', 'supportExpected', 'supportReproduce', 'supportDiagnostics']
    .forEach(id => $(`#${id}`).addEventListener('input', updateSupportPreview));
  $('#copySupportButton').onclick = copySupportNote;
  addEventListener('keydown', event => {
    if (event.ctrlKey && event.key === 'Enter') { event.preventDefault(); if (!$('#startFocusButton').disabled) startFocus(); }
    if (event.ctrlKey && event.key.toLowerCase() === 'k') { event.preventDefault(); showPage('board', 'Work Board'); setTimeout(() => $('#boardSearch').focus(), 50); }
  });
}

async function init() {
  bindEvents();
  $('#dateLabel').textContent = new Intl.DateTimeFormat([], {weekday: 'long', month: 'long', day: 'numeric'}).format(new Date()).toUpperCase();
  const hash = location.hash.replace('#', '');
  if (['command', 'board', 'sessions', 'recovery', 'settings', 'help'].includes(hash)) showPage(hash);
  await loadDashboard();
}

init();
