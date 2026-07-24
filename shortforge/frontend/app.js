'use strict';

const $ = (id) => document.getElementById(id);
let currentEventSource = null;
let refreshTimer = null;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (res.status === 401) {
    window.location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

const ACTIVE = new Set(['queued', 'downloading', 'transcribing', 'analyzing', 'rendering']);
const STAGE_LABEL = {
  queued: 'Queued', downloading: 'Downloading', transcribing: 'Transcribing',
  analyzing: 'Analyzing', rendering: 'Rendering', done: 'Done', error: 'Error',
};

function badgeClass(status) {
  if (status === 'done') return 'done';
  if (status === 'error') return 'error';
  return 'active';
}

// --- LIST VIEW --------------------------------------------------------------

async function loadJobs() {
  let data;
  try { data = await api('/api/jobs'); } catch (_) { return; }
  const wrap = $('jobs');
  if (!data.jobs.length) {
    wrap.innerHTML = '<div class="empty">No videos yet. Paste a YouTube URL above to get started.</div>';
    return;
  }
  wrap.innerHTML = '';
  for (const job of data.jobs) {
    const el = document.createElement('div');
    el.className = 'job';
    const title = job.title || job.url || 'Untitled';
    const count = job.status === 'done' ? `${job.num_shorts} shorts` :
      (ACTIVE.has(job.status) ? `${job.progress}%` : '');
    el.innerHTML = `
      <div class="job-info">
        <div class="job-title">${escapeHtml(title)}</div>
        <div class="job-sub">${STAGE_LABEL[job.status] || job.status} · ${count}</div>
      </div>
      <span class="badge ${badgeClass(job.status)}">${STAGE_LABEL[job.status] || job.status}</span>`;
    el.addEventListener('click', () => openDetail(job.id));
    wrap.appendChild(el);
  }
}

async function createJob() {
  const url = $('job-url').value.trim();
  const err = $('create-error');
  err.textContent = '';
  if (!url) { err.textContent = 'Paste a YouTube URL first.'; return; }
  const btn = $('btn-create');
  btn.disabled = true; btn.textContent = 'Starting…';
  try {
    const body = {
      url,
      count: parseInt($('opt-count').value, 10),
      reframe: $('opt-reframe').value,
      captions: $('opt-captions').checked,
    };
    const { id } = await api('/api/jobs', { method: 'POST', body: JSON.stringify(body) });
    $('job-url').value = '';
    openDetail(id);
    loadJobs();
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = 'Generate shorts';
  }
}

// --- DETAIL VIEW ------------------------------------------------------------

function showView(which) {
  $('view-list').classList.toggle('hidden', which !== 'list');
  $('view-detail').classList.toggle('hidden', which !== 'detail');
}

function closeStreams() {
  if (currentEventSource) { currentEventSource.close(); currentEventSource = null; }
}

async function openDetail(jobId) {
  closeStreams();
  showView('detail');
  $('shorts').innerHTML = '';
  try {
    const job = await api(`/api/jobs/${jobId}`);
    renderDetail(job);
  } catch (_) { showView('list'); return; }

  // Live updates via SSE while the job is active.
  currentEventSource = new EventSource(`/api/jobs/${jobId}/events`);
  currentEventSource.onmessage = (ev) => {
    try { renderDetail(JSON.parse(ev.data)); } catch (_) {}
  };
  currentEventSource.addEventListener('gone', () => closeStreams());
  currentEventSource.onerror = () => { /* keep last state; browser retries */ };
}

function renderDetail(job) {
  $('detail-title').textContent = job.title || job.url || 'Untitled';
  const badge = $('detail-status');
  badge.textContent = STAGE_LABEL[job.status] || job.status;
  badge.className = `badge ${badgeClass(job.status)}`;
  $('detail-message').textContent = job.error || job.message || '';
  $('detail-bar').style.width = `${job.progress || 0}%`;
  $('detail-log').textContent = job.log || '';

  const grid = $('shorts');
  const shorts = job.shorts || [];
  if (!shorts.length) {
    grid.innerHTML = ACTIVE.has(job.status)
      ? '<div class="empty">Working… shorts will appear here as they finish.</div>'
      : (job.status === 'error' ? '' : '<div class="empty">No shorts.</div>');
  } else {
    grid.innerHTML = '';
    for (const s of shorts) grid.appendChild(shortCard(s));
  }
  if (!ACTIVE.has(job.status)) loadJobs();
}

function shortCard(s) {
  const el = document.createElement('div');
  el.className = 'short';
  el.innerHTML = `
    <img class="thumb" src="/api/shorts/${s.id}/thumb" loading="lazy"
         onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb thumb-fallback',textContent:'▶'}))" />
    <div class="short-body">
      <div class="short-title">${escapeHtml(s.title || 'Short')}</div>
      <div class="short-meta">
        <span class="score">★ ${Math.round(s.score)}</span>
        <a class="dl" href="/api/shorts/${s.id}/download">Download</a>
      </div>
    </div>`;
  el.querySelector('.thumb').addEventListener('click', () => openPlayer(s));
  el.querySelector('.short-title').addEventListener('click', () => openPlayer(s));
  return el;
}

// --- PLAYER -----------------------------------------------------------------

function openPlayer(s) {
  const v = $('player-video');
  v.src = `/api/shorts/${s.id}/video`;
  $('player-download').href = `/api/shorts/${s.id}/download`;
  $('player-modal').classList.remove('hidden');
  v.play().catch(() => {});
}
function closePlayer() {
  const v = $('player-video');
  v.pause(); v.src = '';
  $('player-modal').classList.add('hidden');
}

// --- SETTINGS ---------------------------------------------------------------

async function openSettings() {
  try {
    const s = await api('/api/settings');
    $('set-gemini-model').value = s.gemini_model || '';
    $('set-whisper').value = s.whisper_model || 'small';
    $('gemini-status').textContent = s.gemini_api_key_set ? 'Key configured ✓' : 'No key set';
    $('ngrok-status').textContent = s.ngrok_authtoken_set ? 'Token configured ✓' : 'No token set';
    $('set-gemini-key').value = '';
    $('set-ngrok').value = '';
    $('set-password').value = '';
    $('settings-msg').textContent = '';
    $('settings-modal').classList.remove('hidden');
  } catch (_) {}
}

async function saveSettings() {
  const body = {
    gemini_api_key: $('set-gemini-key').value,
    gemini_model: $('set-gemini-model').value,
    whisper_model: $('set-whisper').value,
    ngrok_authtoken: $('set-ngrok').value,
    new_password: $('set-password').value,
  };
  try {
    await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
    $('settings-msg').textContent = 'Saved ✓';
    setTimeout(() => $('settings-modal').classList.add('hidden'), 700);
  } catch (e) {
    $('settings-msg').textContent = e.message;
  }
}

// --- helpers ----------------------------------------------------------------

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// --- wire up ----------------------------------------------------------------

$('btn-create').addEventListener('click', createJob);
$('btn-back').addEventListener('click', () => { closeStreams(); showView('list'); loadJobs(); });
$('btn-settings').addEventListener('click', openSettings);
$('btn-close-settings').addEventListener('click', () => $('settings-modal').classList.add('hidden'));
$('btn-save-settings').addEventListener('click', saveSettings);
$('btn-close-player').addEventListener('click', closePlayer);
$('btn-logout').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  window.location.href = '/login';
});
$('player-modal').addEventListener('click', (e) => { if (e.target.id === 'player-modal') closePlayer(); });
$('settings-modal').addEventListener('click', (e) => { if (e.target.id === 'settings-modal') $('settings-modal').classList.add('hidden'); });

loadJobs();
refreshTimer = setInterval(() => { if (!$('view-list').classList.contains('hidden')) loadJobs(); }, 5000);
