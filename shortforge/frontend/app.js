'use strict';

const $ = (id) => document.getElementById(id);
let currentEventSource = null;
let channelPollTimer = null;
let currentChannelId = null;
let pendingDubShortId = null;
let languagesCache = null;

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
const DUB_ACTIVE = new Set(['queued', 'downloading', 'transcribing', 'translating', 'dubbing', 'rendering']);
const STAGE_LABEL = {
  queued: 'Queued', downloading: 'Downloading', transcribing: 'Transcribing',
  analyzing: 'Analyzing', translating: 'Translating', dubbing: 'Voicing',
  rendering: 'Rendering', done: 'Done', error: 'Error', fetching: 'Fetching', ready: 'Ready',
};

function badgeClass(status) {
  if (status === 'done' || status === 'ready') return 'done';
  if (status === 'error') return 'error';
  return 'active';
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ===================== MODE TABS ===========================================

function switchMode(mode) {
  document.querySelectorAll('.tab').forEach((t) =>
    t.classList.toggle('active', t.dataset.mode === mode));
  $('screen-clip').classList.toggle('hidden', mode !== 'clip');
  $('screen-copy').classList.toggle('hidden', mode !== 'copy');
  if (mode === 'clip') { stopChannelPoll(); loadJobs(); }
  else { closeStreams(); loadChannels(); }
}

// ===================== CLIP MODE (shorts generator) ========================

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
      url, count: parseInt($('opt-count').value, 10),
      reframe: $('opt-reframe').value, captions: $('opt-captions').checked,
    };
    const { id } = await api('/api/jobs', { method: 'POST', body: JSON.stringify(body) });
    $('job-url').value = '';
    openDetail(id); loadJobs();
  } catch (e) { err.textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = 'Generate shorts'; }
}

function showClipView(which) {
  $('view-list').classList.toggle('hidden', which !== 'list');
  $('view-detail').classList.toggle('hidden', which !== 'detail');
}
function closeStreams() {
  if (currentEventSource) { currentEventSource.close(); currentEventSource = null; }
}

async function openDetail(jobId) {
  closeStreams();
  showClipView('detail');
  $('shorts').innerHTML = '';
  try { renderDetail(await api(`/api/jobs/${jobId}`)); }
  catch (_) { showClipView('list'); return; }
  currentEventSource = new EventSource(`/api/jobs/${jobId}/events`);
  currentEventSource.onmessage = (ev) => { try { renderDetail(JSON.parse(ev.data)); } catch (_) {} };
  currentEventSource.addEventListener('gone', () => closeStreams());
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
  const play = () => openPlayer(`/api/shorts/${s.id}/video`, `/api/shorts/${s.id}/download`);
  el.querySelector('.thumb').addEventListener('click', play);
  el.querySelector('.short-title').addEventListener('click', play);
  return el;
}

// ===================== COPY MODE (channels + dubbing) ======================

async function loadChannels() {
  let data;
  try { data = await api('/api/channels'); } catch (_) { return; }
  const wrap = $('channels');
  if (!data.channels.length) {
    wrap.innerHTML = '<div class="empty">No channels yet. Add a YouTube channel above.</div>';
    return;
  }
  wrap.innerHTML = '';
  for (const ch of data.channels) {
    const el = document.createElement('div');
    el.className = 'job';
    el.innerHTML = `
      <div class="job-info">
        <div class="job-title">${escapeHtml(ch.name || ch.url)}</div>
        <div class="job-sub">${ch.short_count} shorts · ${escapeHtml(ch.message || '')}</div>
      </div>
      <span class="badge ${badgeClass(ch.status)}">${STAGE_LABEL[ch.status] || ch.status}</span>`;
    el.addEventListener('click', () => openChannel(ch.id));
    wrap.appendChild(el);
  }
}

async function addChannel() {
  const url = $('channel-url').value.trim();
  const err = $('channel-error');
  err.textContent = '';
  if (!url) { err.textContent = 'Paste a channel URL first.'; return; }
  const btn = $('btn-add-channel');
  btn.disabled = true; btn.textContent = 'Adding…';
  try {
    const { id } = await api('/api/channels', { method: 'POST', body: JSON.stringify({ url }) });
    $('channel-url').value = '';
    openChannel(id);
  } catch (e) { err.textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = 'Add channel'; }
}

function showCopyView(which) {
  $('view-channels').classList.toggle('hidden', which !== 'channels');
  $('view-channel').classList.toggle('hidden', which !== 'channel');
}

function stopChannelPoll() {
  if (channelPollTimer) { clearInterval(channelPollTimer); channelPollTimer = null; }
}

async function openChannel(id) {
  currentChannelId = id;
  showCopyView('channel');
  $('cshorts').innerHTML = '<div class="empty">Loading…</div>';
  await reloadChannel();
  stopChannelPoll();
  channelPollTimer = setInterval(reloadChannel, 3000);
}

async function reloadChannel() {
  if (!currentChannelId) return;
  let data;
  try { data = await api(`/api/channels/${currentChannelId}`); }
  catch (_) { return; }
  renderChannel(data);
}

function renderChannel(ch) {
  $('channel-name').textContent = ch.name || ch.url || 'Channel';
  $('channel-status').textContent =
    (STAGE_LABEL[ch.status] || ch.status) + (ch.message ? ' · ' + ch.message : '');
  const grid = $('cshorts');
  const shorts = ch.shorts || [];
  if (!shorts.length) {
    grid.innerHTML = ch.status === 'fetching'
      ? '<div class="empty">Fetching the channel\'s shorts…</div>'
      : '<div class="empty">No shorts found.</div>';
    return;
  }
  grid.innerHTML = '';
  for (const s of shorts) grid.appendChild(cshortCard(s));
  // Keep polling only while something is moving.
  const busy = ch.status === 'fetching' ||
    shorts.some((s) => (s.dubs || []).some((d) => DUB_ACTIVE.has(d.status)));
  if (!busy) stopChannelPoll();
}

function cshortCard(s) {
  const el = document.createElement('div');
  el.className = 'short';
  const dubsHtml = (s.dubs || []).map((d) => {
    if (d.status === 'done') {
      return `<button class="dub-chip done" data-dub="${d.id}" data-lang="${d.lang}">▶ ${d.lang.toUpperCase()}</button>`;
    }
    if (d.status === 'error') {
      return `<span class="dub-chip err">${d.lang.toUpperCase()} ✕</span>`;
    }
    return `<span class="dub-chip active">${d.lang.toUpperCase()} ${d.progress || 0}%</span>`;
  }).join('');
  el.innerHTML = `
    <img class="thumb" src="${escapeHtml(s.thumb)}" loading="lazy"
         onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb thumb-fallback',textContent:'▶'}))" />
    <div class="short-body">
      <div class="short-title">${escapeHtml(s.title || 'Short')}</div>
      <div class="dub-chips">${dubsHtml}</div>
      <button class="btn ghost small translate-btn" data-short="${s.id}">＋ Translate</button>
    </div>`;
  el.querySelector('.translate-btn').addEventListener('click', () => openLangModal(s.id));
  el.querySelectorAll('.dub-chip.done').forEach((chip) => {
    chip.addEventListener('click', () => {
      const id = chip.dataset.dub;
      openPlayer(`/api/dubs/${id}/video`, `/api/dubs/${id}/download`);
    });
  });
  return el;
}

async function refreshCurrentChannel() {
  if (!currentChannelId) return;
  try { await api(`/api/channels/${currentChannelId}/refresh`, { method: 'POST' }); } catch (_) {}
  stopChannelPoll();
  channelPollTimer = setInterval(reloadChannel, 3000);
  reloadChannel();
}

async function deleteCurrentChannel() {
  if (!currentChannelId) return;
  if (!confirm('Delete this channel and its dubs?')) return;
  try { await api(`/api/channels/${currentChannelId}`, { method: 'DELETE' }); } catch (_) {}
  stopChannelPoll();
  currentChannelId = null;
  showCopyView('channels');
  loadChannels();
}

// --- language picker / start dub -------------------------------------------

async function openLangModal(shortId) {
  pendingDubShortId = shortId;
  if (!languagesCache) {
    try { languagesCache = (await api('/api/languages')).languages; }
    catch (_) { languagesCache = { en: 'English', fr: 'French', es: 'Spanish' }; }
  }
  const sel = $('lang-select');
  sel.innerHTML = '';
  for (const [code, name] of Object.entries(languagesCache)) {
    const o = document.createElement('option');
    o.value = code; o.textContent = `${name} (${code})`;
    sel.appendChild(o);
  }
  sel.value = 'fr';
  $('lang-modal').classList.remove('hidden');
}

async function confirmLang() {
  if (!pendingDubShortId) return;
  const lang = $('lang-select').value;
  try {
    await api(`/api/shorts-src/${pendingDubShortId}/dub`, {
      method: 'POST', body: JSON.stringify({ lang }),
    });
  } catch (e) { alert(e.message); }
  $('lang-modal').classList.add('hidden');
  pendingDubShortId = null;
  stopChannelPoll();
  channelPollTimer = setInterval(reloadChannel, 3000);
  reloadChannel();
}

// ===================== SHARED: player + settings ===========================

function openPlayer(videoUrl, downloadUrl) {
  const v = $('player-video');
  v.src = videoUrl;
  $('player-download').href = downloadUrl;
  $('player-modal').classList.remove('hidden');
  v.play().catch(() => {});
}
function closePlayer() {
  const v = $('player-video');
  v.pause(); v.src = '';
  $('player-modal').classList.add('hidden');
}

async function openSettings() {
  try {
    const s = await api('/api/settings');
    $('set-gemini-model').value = s.gemini_model || '';
    $('set-whisper').value = s.whisper_model || 'small';
    $('gemini-status').textContent = s.gemini_api_key_set ? 'Key configured ✓' : 'No key set';
    $('ngrok-status').textContent = s.ngrok_authtoken_set ? 'Token configured ✓' : 'No token set';
    $('set-gemini-key').value = ''; $('set-ngrok').value = ''; $('set-password').value = '';
    $('settings-msg').textContent = '';
    $('settings-modal').classList.remove('hidden');
  } catch (_) {}
}

async function saveSettings() {
  const body = {
    gemini_api_key: $('set-gemini-key').value, gemini_model: $('set-gemini-model').value,
    whisper_model: $('set-whisper').value, ngrok_authtoken: $('set-ngrok').value,
    new_password: $('set-password').value,
  };
  try {
    await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
    $('settings-msg').textContent = 'Saved ✓';
    setTimeout(() => $('settings-modal').classList.add('hidden'), 700);
  } catch (e) { $('settings-msg').textContent = e.message; }
}

// ===================== wire up =============================================

document.querySelectorAll('.tab').forEach((t) =>
  t.addEventListener('click', () => switchMode(t.dataset.mode)));

$('btn-create').addEventListener('click', createJob);
$('btn-back').addEventListener('click', () => { closeStreams(); showClipView('list'); loadJobs(); });

$('btn-add-channel').addEventListener('click', addChannel);
$('btn-channel-back').addEventListener('click', () => {
  stopChannelPoll(); currentChannelId = null; showCopyView('channels'); loadChannels();
});
$('btn-refresh-channel').addEventListener('click', refreshCurrentChannel);
$('btn-delete-channel').addEventListener('click', deleteCurrentChannel);
$('btn-close-lang').addEventListener('click', () => $('lang-modal').classList.add('hidden'));
$('btn-confirm-lang').addEventListener('click', confirmLang);

$('btn-settings').addEventListener('click', openSettings);
$('btn-close-settings').addEventListener('click', () => $('settings-modal').classList.add('hidden'));
$('btn-save-settings').addEventListener('click', saveSettings);
$('btn-close-player').addEventListener('click', closePlayer);
$('btn-logout').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  window.location.href = '/login';
});
[['player-modal', closePlayer], ['settings-modal', () => $('settings-modal').classList.add('hidden')],
 ['lang-modal', () => $('lang-modal').classList.add('hidden')]].forEach(([id, fn]) => {
  $(id).addEventListener('click', (e) => { if (e.target.id === id) fn(); });
});

loadJobs();
setInterval(() => {
  if (!$('screen-clip').classList.contains('hidden') && !$('view-list').classList.contains('hidden')) loadJobs();
}, 5000);
