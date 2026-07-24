'use strict';

const $ = (id) => document.getElementById(id);
let currentEventSource = null;
let dubEventSource = null;
let channelPollTimer = null;
let currentChannelId = null;
let currentMyChannelId = null;
let currentJobId = null;
let currentDubId = null;
let pendingDubShortId = null;
let languagesCache = null;

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (res.status === 401) { window.location.href = '/login'; throw new Error('unauthorized'); }
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
  canceled: 'Canceled',
};

function badgeClass(status) {
  if (status === 'done' || status === 'ready') return 'done';
  if (status === 'error') return 'error';
  return 'active';
}
function escapeHtml(str) {
  return String(str == null ? '' : str).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ===================== MODE + SUB TABS =====================================

function switchMode(mode) {
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.mode === mode));
  $('screen-clip').classList.toggle('hidden', mode !== 'clip');
  $('screen-copy').classList.toggle('hidden', mode !== 'copy');
  $('screen-logs').classList.toggle('hidden', mode !== 'logs');
  if (mode === 'clip') { stopChannelPoll(); loadJobs(); }
  else if (mode === 'logs') { stopChannelPoll(); closeStreams(); loadLogs(); }
  else { closeStreams(); switchSub('sources'); }
}

// ===================== TOOL LOGS ===========================================

let logFilter = '';

async function loadLogs() {
  let data;
  try { data = await api(`/api/activity?kind=${encodeURIComponent(logFilter)}&limit=300`); }
  catch (_) { return; }
  const wrap = $('logs');
  const rows = data.activity || [];
  if (!rows.length) { wrap.innerHTML = '<div class="empty">Nothing logged yet.</div>'; return; }
  const ICON = { dub: '🎙️', clip: '✂️', publish: '🚀' };
  wrap.innerHTML = '';
  for (const a of rows) {
    const el = document.createElement('div'); el.className = 'job';
    const cls = a.status === 'success' ? 'done' : (a.status === 'error' ? 'error' : 'active');
    const when = new Date(a.created_at * 1000).toLocaleString();
    el.innerHTML = `<div class="job-info">
        <div class="job-title">${ICON[a.kind] || '•'} ${escapeHtml(a.title)}</div>
        <div class="job-sub">${when}${a.detail ? ' · ' + escapeHtml(a.detail) : ''}</div>
      </div><span class="badge ${cls}">${a.status}</span>`;
    if (a.ref_type === 'dub' && a.ref_id) {
      el.style.cursor = 'pointer';
      el.addEventListener('click', () => openDubModal(a.ref_id));
    } else if (a.ref_type === 'job' && a.ref_id) {
      el.style.cursor = 'pointer';
      el.addEventListener('click', () => { switchMode('clip'); openDetail(a.ref_id); });
    }
    wrap.appendChild(el);
  }
}

function switchSub(sub) {
  document.querySelectorAll('.subtab').forEach((t) => t.classList.toggle('active', t.dataset.sub === sub));
  $('sub-sources').classList.toggle('hidden', sub !== 'sources');
  $('sub-mine').classList.toggle('hidden', sub !== 'mine');
  if (sub === 'sources') loadChannels();
  else { stopChannelPoll(); loadMyChannels(); }
}

// ===================== CLIP MODE ===========================================

async function loadJobs() {
  let data; try { data = await api('/api/jobs'); } catch (_) { return; }
  const wrap = $('jobs');
  if (!data.jobs.length) { wrap.innerHTML = '<div class="empty">No videos yet. Paste a YouTube URL above to get started.</div>'; return; }
  wrap.innerHTML = '';
  for (const job of data.jobs) {
    const el = document.createElement('div');
    el.className = 'job';
    const title = job.title || job.url || 'Untitled';
    const count = job.status === 'done' ? `${job.num_shorts} shorts` : (ACTIVE.has(job.status) ? `${job.progress}%` : '');
    el.innerHTML = `<div class="job-info"><div class="job-title">${escapeHtml(title)}</div>
      <div class="job-sub">${STAGE_LABEL[job.status] || job.status} · ${count}</div></div>
      <span class="badge ${badgeClass(job.status)}">${STAGE_LABEL[job.status] || job.status}</span>
      <button class="row-del" title="Delete">🗑</button>`;
    el.querySelector('.job-info').addEventListener('click', () => openDetail(job.id));
    el.querySelector('.badge').addEventListener('click', () => openDetail(job.id));
    el.querySelector('.row-del').addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Delete this video and its shorts?')) return;
      try { await api(`/api/jobs/${job.id}`, { method: 'DELETE' }); } catch (_) {}
      loadJobs();
    });
    wrap.appendChild(el);
  }
}

async function createJob() {
  const url = $('job-url').value.trim();
  const err = $('create-error'); err.textContent = '';
  if (!url) { err.textContent = 'Paste a YouTube URL first.'; return; }
  const btn = $('btn-create'); btn.disabled = true; btn.textContent = 'Starting…';
  try {
    const body = { url, count: parseInt($('opt-count').value, 10), reframe: $('opt-reframe').value,
      captions: $('opt-captions').checked, music_id: $('opt-music').value,
      caption_style: $('opt-caption-style').value };
    const { id } = await api('/api/jobs', { method: 'POST', body: JSON.stringify(body) });
    $('job-url').value = ''; openDetail(id); loadJobs();
  } catch (e) { err.textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = 'Generate shorts'; }
}

function showClipView(which) {
  $('view-list').classList.toggle('hidden', which !== 'list');
  $('view-detail').classList.toggle('hidden', which !== 'detail');
}
function closeStreams() { if (currentEventSource) { currentEventSource.close(); currentEventSource = null; } }

async function openDetail(jobId) {
  currentJobId = jobId;
  closeStreams(); showClipView('detail'); $('shorts').innerHTML = '';
  try { renderDetail(await api(`/api/jobs/${jobId}`)); } catch (_) { showClipView('list'); return; }
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
  const grid = $('shorts'); const shorts = job.shorts || [];
  if (!shorts.length) {
    grid.innerHTML = ACTIVE.has(job.status) ? '<div class="empty">Working… shorts will appear here as they finish.</div>'
      : (job.status === 'error' ? '' : '<div class="empty">No shorts.</div>');
  } else { grid.innerHTML = ''; for (const s of shorts) grid.appendChild(shortCard(s)); }
  if (!ACTIVE.has(job.status)) loadJobs();
}

function shortCard(s) {
  const el = document.createElement('div'); el.className = 'short';
  el.innerHTML = `<img class="thumb" src="/api/shorts/${s.id}/thumb" loading="lazy"
    onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb thumb-fallback',textContent:'▶'}))" />
    <div class="short-body"><div class="short-title">${escapeHtml(s.title || 'Short')}</div>
      <div class="short-meta"><span class="score">★ ${Math.round(s.score)}</span>
      <a class="dl" href="/api/shorts/${s.id}/download">Download</a></div></div>`;
  const play = () => openPlayer(`/api/shorts/${s.id}/video`, `/api/shorts/${s.id}/download`);
  el.querySelector('.thumb').addEventListener('click', play);
  el.querySelector('.short-title').addEventListener('click', play);
  return el;
}

// ===================== COPY: SOURCE CHANNELS ===============================

async function loadChannels() {
  let data; try { data = await api('/api/channels'); } catch (_) { return; }
  const wrap = $('channels');
  if (!data.channels.length) { wrap.innerHTML = '<div class="empty">No channels yet. Add a YouTube channel above.</div>'; return; }
  wrap.innerHTML = '';
  for (const ch of data.channels) {
    const el = document.createElement('div'); el.className = 'job';
    el.innerHTML = `<div class="job-info"><div class="job-title">${escapeHtml(ch.name || ch.url)}</div>
      <div class="job-sub">${ch.short_count} shorts · ${escapeHtml(ch.message || '')}</div></div>
      <span class="badge ${badgeClass(ch.status)}">${STAGE_LABEL[ch.status] || ch.status}</span>`;
    el.addEventListener('click', () => openChannel(ch.id));
    wrap.appendChild(el);
  }
}

async function addChannel() {
  const url = $('channel-url').value.trim();
  const err = $('channel-error'); err.textContent = '';
  if (!url) { err.textContent = 'Paste a channel URL first.'; return; }
  const btn = $('btn-add-channel'); btn.disabled = true; btn.textContent = 'Adding…';
  try {
    const { id } = await api('/api/channels', { method: 'POST', body: JSON.stringify({ url }) });
    $('channel-url').value = ''; openChannel(id);
  } catch (e) { err.textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = 'Add channel'; }
}

function showSourcesView(which) {
  $('view-channels').classList.toggle('hidden', which !== 'channels');
  $('view-channel').classList.toggle('hidden', which !== 'channel');
}
function stopChannelPoll() { if (channelPollTimer) { clearInterval(channelPollTimer); channelPollTimer = null; } }

async function openChannel(id) {
  currentChannelId = id; showSourcesView('channel');
  $('cshorts').innerHTML = '<div class="empty">Loading…</div>';
  await reloadChannel();
  stopChannelPoll(); channelPollTimer = setInterval(reloadChannel, 3000);
}

async function reloadChannel() {
  if (!currentChannelId) return;
  let data; try { data = await api(`/api/channels/${currentChannelId}`); } catch (_) { return; }
  renderChannel(data);
}

function renderChannel(ch) {
  $('channel-name').textContent = ch.name || ch.url || 'Channel';
  $('channel-status').textContent = (STAGE_LABEL[ch.status] || ch.status) + (ch.message ? ' · ' + ch.message : '');
  const grid = $('cshorts'); const shorts = ch.shorts || [];
  if (!shorts.length) {
    grid.innerHTML = ch.status === 'fetching' ? '<div class="empty">Fetching the channel\'s shorts…</div>' : '<div class="empty">No shorts found.</div>';
    return;
  }
  grid.innerHTML = ''; for (const s of shorts) grid.appendChild(cshortCard(s));
  const busy = ch.status === 'fetching' || shorts.some((s) => (s.dubs || []).some((d) => DUB_ACTIVE.has(d.status)));
  if (!busy) stopChannelPoll();
}

function cshortCard(s) {
  const el = document.createElement('div'); el.className = 'short';
  const dubsHtml = (s.dubs || []).map((d) => {
    const cls = d.status === 'done' ? 'done' : (d.status === 'error' ? 'err' : 'active');
    const label = d.status === 'done' ? `▶ ${d.lang.toUpperCase()}` : (d.status === 'error' ? `${d.lang.toUpperCase()} ✕` : `${d.lang.toUpperCase()} ${d.progress || 0}%`);
    return `<button class="dub-chip ${cls}" data-dub="${d.id}">${label}</button>`;
  }).join('');
  el.innerHTML = `<img class="thumb" src="${escapeHtml(s.thumb)}" loading="lazy"
    onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb thumb-fallback',textContent:'▶'}))" />
    <div class="short-body"><div class="short-title">${escapeHtml(s.title || 'Short')}</div>
      <div class="dub-chips">${dubsHtml}</div>
      <button class="btn ghost small translate-btn" data-short="${s.id}">＋ Translate</button></div>`;
  el.querySelector('.translate-btn').addEventListener('click', () => openLangModal(s.id));
  el.querySelectorAll('.dub-chip').forEach((chip) => chip.addEventListener('click', () => openDubModal(chip.dataset.dub)));
  return el;
}

async function refreshCurrentChannel() {
  if (!currentChannelId) return;
  try { await api(`/api/channels/${currentChannelId}/refresh`, { method: 'POST' }); } catch (_) {}
  stopChannelPoll(); channelPollTimer = setInterval(reloadChannel, 3000); reloadChannel();
}
async function deleteCurrentChannel() {
  if (!currentChannelId || !confirm('Delete this channel and its dubs?')) return;
  try { await api(`/api/channels/${currentChannelId}`, { method: 'DELETE' }); } catch (_) {}
  stopChannelPoll(); currentChannelId = null; showSourcesView('channels'); loadChannels();
}

// ===================== COPY: MY CHANNELS (destinations) ====================

async function loadMyChannels() {
  let data; try { data = await api('/api/my-channels'); } catch (_) { return; }
  const wrap = $('mychannels');
  if (!data.channels.length) { wrap.innerHTML = '<div class="empty">No channels yet. Create one to organize your translated videos.</div>'; return; }
  wrap.innerHTML = '';
  for (const ch of data.channels) {
    const el = document.createElement('div'); el.className = 'job';
    if (ch.kind === 'youtube') {
      const auto = ch.auto_enabled ? ' · <span class="score">AUTO</span>' : '';
      el.innerHTML = `<div class="job-info"><div class="job-title">▶ ${escapeHtml(ch.name)}</div>
        <div class="job-sub">${ch.video_count} published · ${ch.pending_count || 0} queued${auto}</div></div>
        <span class="badge yt-badge">YouTube</span>`;
      el.addEventListener('click', () => openYtChannel(ch.id));
    } else if (ch.kind === 'tiktok') {
      const auto = ch.auto_enabled ? ' · <span class="score">AUTO</span>' : '';
      el.innerHTML = `<div class="job-info"><div class="job-title">♪ ${escapeHtml(ch.name)}</div>
        <div class="job-sub">${ch.video_count} posted · ${ch.pending_count || 0} queued${auto}</div></div>
        <span class="badge tt-badge">TikTok</span>`;
      el.addEventListener('click', () => openTtAccount(ch.id));
    } else {
      el.innerHTML = `<div class="job-info"><div class="job-title">${escapeHtml(ch.name)}</div>
        <div class="job-sub">${ch.video_count} translated videos</div></div>
        <span class="badge">📺</span>`;
      el.addEventListener('click', () => openMyChannel(ch.id));
    }
    wrap.appendChild(el);
  }
}

// --- connected YouTube channel detail + auto mode --------------------------

let currentYtChannelId = null;

function showMineView(which) {
  $('view-mychannels').classList.toggle('hidden', which !== 'list');
  $('view-mychannel').classList.toggle('hidden', which !== 'detail');
  $('view-ytchannel').classList.toggle('hidden', which !== 'yt');
  $('view-ttaccount').classList.toggle('hidden', which !== 'tt');
}

async function openYtChannel(id) {
  currentYtChannelId = id;
  showMineView('yt');
  // Populate source channels + languages for the auto form.
  try {
    const sources = (await api('/api/channels')).channels;
    $('yt-sources').innerHTML = sources.length ? '' : '<span class="muted">Add source channels in the Sources tab first.</span>';
    for (const sc of sources) {
      const id2 = `src-${sc.id}`;
      const row = document.createElement('label'); row.className = 'check-row';
      row.innerHTML = `<input type="checkbox" value="${sc.id}" id="${id2}" /> ${escapeHtml(sc.name || sc.url)}`;
      $('yt-sources').appendChild(row);
    }
  } catch (_) {}
  await loadCaptionStyles();
  await loadMusicOptions();
  if (!languagesCache) { try { languagesCache = (await api('/api/languages')).languages; } catch (_) { languagesCache = { fr: 'French' }; } }
  const ls = $('yt-lang'); ls.innerHTML = '';
  for (const [c, n] of Object.entries(languagesCache)) { const o = document.createElement('option'); o.value = c; o.textContent = `${n} (${c})`; ls.appendChild(o); }
  ls.value = 'fr';
  await reloadYtChannel();
  stopChannelPoll(); channelPollTimer = setInterval(reloadYtChannel, 5000);
}

async function reloadYtChannel() {
  if (!currentYtChannelId) return;
  let d; try { d = await api(`/api/youtube/channels/${currentYtChannelId}`); } catch (_) { return; }
  $('ytchannel-name').textContent = '▶ ' + (d.title || 'Channel');
  $('yt-stat-published').textContent = d.stats.published;
  $('yt-stat-pending').textContent = d.stats.pending;
  $('yt-stat-errors').textContent = d.stats.errors;
  $('yt-auto-enabled').checked = !!d.auto_enabled;
  // Apply saved config to the form (once per load).
  const cfg = d.auto_config || {};
  if (cfg.target_lang) $('yt-lang').value = cfg.target_lang;
  if (cfg.cadence_mode) $('yt-cadence').value = cfg.cadence_mode;
  if (cfg.selection) $('yt-selection').value = cfg.selection;
  if (cfg.count) $('yt-count').value = cfg.count;
  if (cfg.privacy) $('yt-privacy').value = cfg.privacy;
  if (cfg.per_day) $('yt-perday').value = cfg.per_day;
  if (cfg.times) $('yt-times').value = (cfg.times || []).join(',');
  if (cfg.caption_style !== undefined) $('yt-captions').value = cfg.caption_style || '';
  if (cfg.music_id !== undefined) $('yt-music').value = cfg.music_id || '';
  for (const scid of (cfg.source_channel_ids || [])) { const cb = $(`src-${scid}`); if (cb) cb.checked = true; }
  toggleCadenceFields(); toggleSelectionFields();
  renderYtItems(d.items || []);
}

function renderYtItems(items) {
  const wrap = $('yt-items');
  if (!items.length) { wrap.innerHTML = '<div class="empty">Nothing queued yet. Enable Auto or publish a dub here.</div>'; return; }
  wrap.innerHTML = '';
  for (const it of items) {
    const el = document.createElement('div'); el.className = 'job';
    const cls = it.status === 'published' ? 'done' : (it.status === 'error' ? 'error' : 'active');
    const when = it.scheduled_at ? new Date(it.scheduled_at * 1000).toLocaleString() : '';
    let views = '';
    if (it.views_translated != null) {
      const orig = it.views_original != null ? ` vs ${it.views_original} orig` : '';
      views = ` · 👁 ${it.views_translated}${orig}`;
    }
    const dubState = it.dub_status && it.dub_status !== 'done' ? ` · dub: ${STAGE_LABEL[it.dub_status] || it.dub_status}` : '';
    el.innerHTML = `<div class="job-info"><div class="job-title">${escapeHtml(it.title || 'Short')} <small class="muted">${it.lang.toUpperCase()}</small></div>
      <div class="job-sub">${STAGE_LABEL[it.status] || it.status} · ${when}${dubState}${views}${it.error ? ' · ' + escapeHtml(it.error) : ''}</div></div>
      <span class="badge ${cls}">${it.status}</span>`;
    el.style.cursor = 'pointer';
    el.addEventListener('click', () => {
      if (it.yt_video_id) window.open(`https://youtu.be/${it.yt_video_id}`, '_blank');
      else if (it.dub_id) openDubModal(it.dub_id);  // inspect voice / log / errors
    });
    wrap.appendChild(el);
  }
}

function toggleCadenceFields() { $('yt-manual-cadence').style.display = $('yt-cadence').value === 'manual' ? 'flex' : 'none'; }
function toggleSelectionFields() { $('yt-count-wrap').style.display = $('yt-selection').value === 'number' ? 'block' : 'none'; }

async function saveAuto() {
  const sources = Array.from($('yt-sources').querySelectorAll('input:checked')).map((c) => c.value);
  const cfg = {
    source_channel_ids: sources,
    target_lang: $('yt-lang').value,
    cadence_mode: $('yt-cadence').value,
    per_day: parseInt($('yt-perday').value, 10),
    times: $('yt-times').value.split(',').map((t) => t.trim()).filter(Boolean),
    selection: $('yt-selection').value,
    count: parseInt($('yt-count').value, 10) || 30,
    privacy: $('yt-privacy').value,
    caption_style: $('yt-captions').value,
    music_id: $('yt-music').value,
  };
  const body = { enabled: $('yt-auto-enabled').checked, config: cfg };
  const msg = $('yt-auto-msg'); msg.textContent = 'Saving…';
  if (body.enabled && !cfg.source_channel_ids.length) {
    msg.textContent = 'Tick at least one source channel first.'; return;
  }
  try {
    const r = await api(`/api/youtube/channels/${currentYtChannelId}/auto`, { method: 'POST', body: JSON.stringify(body) });
    if (body.enabled) {
      msg.textContent = r.started > 0
        ? `Auto mode active ✓ — ${r.started} videos queued for translation & publishing.`
        : 'Auto mode active ✓ — no new videos to queue (already handled, or sources have no shorts yet).';
    } else { msg.textContent = 'Saved ✓'; }
    reloadYtChannel();
  } catch (e) { msg.textContent = e.message; }
}

async function addMyChannel() {
  const name = $('mychannel-name').value.trim();
  const err = $('mychannel-error'); err.textContent = '';
  if (!name) { err.textContent = 'Enter a channel name.'; return; }
  try {
    await api('/api/my-channels', { method: 'POST', body: JSON.stringify({ name }) });
    $('mychannel-name').value = ''; loadMyChannels();
  } catch (e) { err.textContent = e.message; }
}

async function openMyChannel(id) {
  currentMyChannelId = id; showMineView('detail');
  $('mydubs').innerHTML = '<div class="empty">Loading…</div>';
  await reloadMyChannel();
  stopChannelPoll(); channelPollTimer = setInterval(reloadMyChannel, 3000);
}

async function reloadMyChannel() {
  if (!currentMyChannelId) return;
  let data; try { data = await api(`/api/my-channels/${currentMyChannelId}`); } catch (_) { return; }
  $('mychannel-name-h').textContent = data.name;
  const dubs = data.dubs || [];
  const done = dubs.filter((d) => d.status === 'done');
  $('mychannel-status').textContent = `${done.length} translated · ${dubs.length - done.length} in progress`;
  const grid = $('mydubs');
  if (!dubs.length) { grid.innerHTML = '<div class="empty">No translated videos here yet. Translate a short and send it to this channel.</div>'; return; }
  grid.innerHTML = '';
  for (const d of dubs) grid.appendChild(myDubCard(d));
  if (!dubs.some((d) => DUB_ACTIVE.has(d.status))) stopChannelPoll();
}

function myDubCard(d) {
  const el = document.createElement('div'); el.className = 'short';
  const title = d.tr_title || d.title || 'Dub';
  const thumb = d.status === 'done'
    ? `<img class="thumb" src="/api/dubs/${d.id}/thumb" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb thumb-fallback',textContent:'▶'}))" />`
    : `<div class="thumb thumb-fallback">${d.status === 'error' ? '✕' : (d.progress || 0) + '%'}</div>`;
  el.innerHTML = `${thumb}<div class="short-body">
    <div class="short-title">${escapeHtml(title)}</div>
    <div class="short-meta"><span class="score">${d.lang.toUpperCase()}</span>
      <span class="dl">${STAGE_LABEL[d.status] || d.status}</span></div></div>`;
  el.addEventListener('click', () => openDubModal(d.id));
  return el;
}

async function deleteCurrentMyChannel() {
  if (!currentMyChannelId || !confirm('Delete this channel? (Translated videos are kept.)')) return;
  try { await api(`/api/my-channels/${currentMyChannelId}`, { method: 'DELETE' }); } catch (_) {}
  stopChannelPoll(); currentMyChannelId = null; showMineView('list'); loadMyChannels();
}

// ===================== LANGUAGE PICKER + DUB DETAIL ========================

async function openLangModal(shortId) {
  pendingDubShortId = shortId;
  if (!languagesCache) {
    try { languagesCache = (await api('/api/languages')).languages; }
    catch (_) { languagesCache = { en: 'English', fr: 'French', es: 'Spanish' }; }
  }
  const sel = $('lang-select'); sel.innerHTML = '';
  for (const [code, name] of Object.entries(languagesCache)) {
    const o = document.createElement('option'); o.value = code; o.textContent = `${name} (${code})`; sel.appendChild(o);
  }
  sel.value = 'fr';
  await loadMusicOptions();
  await loadCaptionStyles();
  // Populate destination channels.
  const dest = $('dest-select'); dest.innerHTML = '<option value="">— None —</option>';
  try {
    const mine = (await api('/api/my-channels')).channels;
    for (const ch of mine) {
      const o = document.createElement('option'); o.value = ch.id;
      o.textContent = ch.kind === 'youtube' ? `▶ ${ch.name} (YouTube — publishes)` : ch.name;
      dest.appendChild(o);
    }
  } catch (_) {}
  $('lang-modal').classList.remove('hidden');
}

async function confirmLang() {
  if (!pendingDubShortId) return;
  const body = { lang: $('lang-select').value, dest_channel_id: $('dest-select').value,
    music_id: $('dub-music').value, caption_style: $('dub-captions').value || 'none' };
  try { await api(`/api/shorts-src/${pendingDubShortId}/dub`, { method: 'POST', body: JSON.stringify(body) }); }
  catch (e) { alert(e.message); }
  $('lang-modal').classList.add('hidden'); pendingDubShortId = null;
  if (currentChannelId) { stopChannelPoll(); channelPollTimer = setInterval(reloadChannel, 3000); reloadChannel(); }
}

function closeDubStream() { if (dubEventSource) { dubEventSource.close(); dubEventSource = null; } }

async function openDubModal(dubId) {
  currentDubId = dubId;
  closeDubStream();
  $('dub-modal').classList.remove('hidden');
  try { renderDub(await api(`/api/dubs/${dubId}`)); } catch (_) { return; }
  dubEventSource = new EventSource(`/api/dubs/${dubId}/events`);
  dubEventSource.onmessage = (ev) => { try { renderDub(JSON.parse(ev.data)); } catch (_) {} };
  dubEventSource.addEventListener('gone', () => closeDubStream());
}

function renderDub(d) {
  $('dub-modal-title').textContent = (d.tr_title || d.title || 'Dub') + ` · ${d.lang.toUpperCase()}`;
  const badge = $('dub-status'); badge.textContent = STAGE_LABEL[d.status] || d.status; badge.className = `badge ${badgeClass(d.status)}`;
  $('dub-message').textContent = d.error || d.message || '';
  $('dub-bar').style.width = `${d.progress || 0}%`;
  $('dub-log').textContent = d.log || '';
  const actions = $('dub-actions');
  if (d.status === 'done') {
    actions.classList.remove('hidden');
    $('dub-play').onclick = () => openPlayer(`/api/dubs/${d.id}/video`, `/api/dubs/${d.id}/download`);
    $('dub-dl').href = `/api/dubs/${d.id}/download`;
  } else { actions.classList.add('hidden'); }
  // Metadata (translated title/description + original tags).
  const meta = $('dub-meta'); meta.innerHTML = '';
  const rows = [];
  if (d.tr_title) rows.push(['Titre traduit', d.tr_title]);
  if (d.tr_description) rows.push(['Description traduite', d.tr_description]);
  if (d.tags) rows.push(['Tags', d.tags]);
  meta.innerHTML = rows.map(([label, val]) =>
    `<div class="meta-block"><div class="meta-label">${label}</div><div class="meta-val">${escapeHtml(val)}</div></div>`).join('');
  if (d.status === 'done' && currentMyChannelId) reloadMyChannel();
}

// ===================== CAPTION STYLES ======================================

let captionStyles = null;

async function loadCaptionStyles() {
  if (!captionStyles) {
    try { const r = await api('/api/caption-styles'); captionStyles = r.styles; }
    catch (_) { captionStyles = { karaoke: 'Karaoke' }; }
  }
  // Selectors that require a style (no "none" option).
  for (const id of ['opt-caption-style', 'set-default-caption']) {
    const sel = $(id); if (!sel) continue;
    const prev = sel.value;
    sel.innerHTML = '';
    for (const [k, label] of Object.entries(captionStyles)) {
      const o = document.createElement('option'); o.value = k; o.textContent = label; sel.appendChild(o);
    }
    if (prev) sel.value = prev;
  }
  // Selectors where captions are optional.
  for (const id of ['dub-captions', 'set-default-dub-captions', 'yt-captions']) {
    const sel = $(id); if (!sel) continue;
    const prev = sel.value;
    sel.innerHTML = '<option value="">— None —</option>';
    for (const [k, label] of Object.entries(captionStyles)) {
      const o = document.createElement('option'); o.value = k; o.textContent = label; sel.appendChild(o);
    }
    sel.value = prev;
  }
  return captionStyles;
}

// ===================== MUSIC + MANAGEMENT ==================================

async function loadMusicOptions() {
  let tracks = [];
  try { tracks = (await api('/api/music')).music; } catch (_) {}
  for (const selId of ['opt-music', 'dub-music', 'set-default-music']) {
    const sel = $(selId); if (!sel) continue;
    const prev = sel.value;
    sel.innerHTML = '<option value="">— None —</option>';
    for (const t of tracks) { const o = document.createElement('option'); o.value = t.id; o.textContent = t.name; sel.appendChild(o); }
    sel.value = prev;
  }
  return tracks;
}

async function renderMusicList() {
  const tracks = await loadMusicOptions();
  const wrap = $('music-list'); if (!wrap) return;
  wrap.innerHTML = tracks.length ? '' : '<span class="muted">No tracks uploaded.</span>';
  for (const t of tracks) {
    const row = document.createElement('div'); row.className = 'music-row';
    row.innerHTML = `<span>🎵 ${escapeHtml(t.name)}</span><button class="row-del" title="Delete">🗑</button>`;
    row.querySelector('.row-del').addEventListener('click', async () => {
      try { await api(`/api/music/${t.id}`, { method: 'DELETE' }); } catch (_) {}
      renderMusicList();
    });
    wrap.appendChild(row);
  }
}

async function uploadMusic() {
  const input = $('music-file');
  if (!input.files || !input.files[0]) { return; }
  const btn = $('btn-upload-music'); btn.disabled = true; btn.textContent = 'Uploading…';
  const fd = new FormData(); fd.append('file', input.files[0]);
  try {
    const res = await fetch('/api/music', { method: 'POST', body: fd });
    if (!res.ok) throw new Error('upload failed');
    input.value = ''; await renderMusicList();
  } catch (_) {}
  finally { btn.disabled = false; btn.textContent = 'Upload'; }
}

async function clearFailed() {
  if (!confirm('Delete all failed videos?')) return;
  try { await api('/api/jobs/clear-failed', { method: 'POST' }); } catch (_) {}
  loadJobs();
}

async function retryJob() {
  if (!currentJobId) return;
  try { await api(`/api/jobs/${currentJobId}/retry`, { method: 'POST' }); } catch (_) {}
  openDetail(currentJobId);
}
async function deleteJob() {
  if (!currentJobId || !confirm('Delete this video and its shorts?')) return;
  try { await api(`/api/jobs/${currentJobId}`, { method: 'DELETE' }); } catch (_) {}
  closeStreams(); showClipView('list'); loadJobs();
}

async function retryDub() {
  if (!currentDubId) return;
  try { await api(`/api/dubs/${currentDubId}/retry`, { method: 'POST' }); } catch (_) {}
  openDubModal(currentDubId);
}
async function deleteDub() {
  if (!currentDubId || !confirm('Delete this dub?')) return;
  try { await api(`/api/dubs/${currentDubId}`, { method: 'DELETE' }); } catch (_) {}
  closeDubStream(); $('dub-modal').classList.add('hidden');
  if (currentChannelId) reloadChannel();
  if (currentMyChannelId) reloadMyChannel();
}

// ===================== SHARED: player + settings ===========================

function openPlayer(videoUrl, downloadUrl) {
  const v = $('player-video'); v.src = videoUrl;
  $('player-download').href = downloadUrl;
  $('player-modal').classList.remove('hidden'); v.play().catch(() => {});
}
function closePlayer() { const v = $('player-video'); v.pause(); v.src = ''; $('player-modal').classList.add('hidden'); }

async function openSettings() {
  try {
    const s = await api('/api/settings');
    $('set-gemini-model').value = s.gemini_model || '';
    $('set-whisper').value = s.whisper_model || 'small';
    $('set-tts').value = s.tts_engine || 'kokoro';
    $('gemini-status').textContent = s.gemini_api_key_set ? 'Key configured ✓' : 'No key set';
    $('ngrok-status').textContent = s.ngrok_authtoken_set ? 'Token configured ✓' : 'No token set';
    $('set-gemini-key').value = ''; $('set-ngrok').value = ''; $('set-password').value = '';
    $('settings-msg').textContent = '';
    await renderMusicList();
    await loadCaptionStyles();
    $('set-default-music').value = s.default_dub_music || '';
    $('set-default-caption').value = s.default_caption_style || 'karaoke';
    $('set-default-dub-captions').value = s.default_dub_captions || '';
    $('set-gclient').value = '';
    $('gclient-status').textContent = s.google_client_id_set ? 'Client ID set ✓' : 'Not set';
    $('gsecret-status').textContent = s.google_client_secret_set ? 'Secret set ✓' : 'Not set';
    try {
      const yc = await api('/api/youtube/config');
      $('yt-redirect').textContent = yc.redirect_uri;
      $('yt-origin').textContent = yc.js_origin;
    } catch (_) {}
    renderYtAccounts();
    $('ttkey-status').textContent = s.tiktok_client_key_set ? 'Client key set ✓' : 'Not set';
    $('ttsecret-status').textContent = s.tiktok_client_secret_set ? 'Secret set ✓' : 'Not set';
    $('set-ttkey').value = ''; $('set-ttsecret').value = '';
    try {
      const tc = await api('/api/tiktok/config');
      $('tt-redirect').textContent = tc.redirect_uri;
      $('tt-origin').textContent = tc.js_origin;
    } catch (_) {}
    renderTtAccounts();
    try { $('service-cmd').textContent = (await api('/api/system/service')).command; } catch (_) {}
    $('settings-modal').classList.remove('hidden');
  } catch (_) {}
}

async function renderYtAccounts() {
  let accounts = [];
  try { accounts = (await api('/api/youtube/accounts')).accounts; } catch (_) {}
  const wrap = $('yt-accounts');
  wrap.innerHTML = accounts.length ? '' : '<span class="muted">No Google account connected.</span>';
  for (const a of accounts) {
    const row = document.createElement('div'); row.className = 'music-row';
    const chans = a.channels.map((c) => c.title).join(', ') || 'no channels';
    row.innerHTML = `<span>▶ ${escapeHtml(a.email || 'account')} <small class="muted">(${escapeHtml(chans)})</small></span><button class="row-del" title="Disconnect">🗑</button>`;
    row.querySelector('.row-del').addEventListener('click', async () => {
      if (!confirm('Disconnect this Google account?')) return;
      try { await api(`/api/youtube/accounts/${a.id}`, { method: 'DELETE' }); } catch (_) {}
      renderYtAccounts();
    });
    wrap.appendChild(row);
  }
}

// --- TikTok accounts (settings) --------------------------------------------

async function renderTtAccounts() {
  let accounts = [];
  try { accounts = (await api('/api/tiktok/accounts')).accounts; } catch (_) {}
  const wrap = $('tt-accounts');
  wrap.innerHTML = accounts.length ? '' : '<span class="muted">No TikTok account connected.</span>';
  for (const a of accounts) {
    const row = document.createElement('div'); row.className = 'music-row';
    const badge = a.mode === 'browser' ? 'browser' : 'API';
    row.innerHTML = `<span>♪ ${escapeHtml(a.name)} <small class="muted">(${badge})</small></span><button class="row-del" title="Disconnect">🗑</button>`;
    row.querySelector('.row-del').addEventListener('click', async () => {
      if (!confirm('Disconnect this TikTok account?')) return;
      try { await api(`/api/tiktok/accounts/${a.id}`, { method: 'DELETE' }); } catch (_) {}
      renderTtAccounts();
    });
    wrap.appendChild(row);
  }
}

async function connectTikTokApi() {
  const mode = $('set-tt-mode').value || 'direct';
  try {
    const { url } = await api(`/api/tiktok/auth-url?mode=${encodeURIComponent(mode)}`);
    window.location.href = url;
  } catch (e) { alert(e.message); }
}

// --- remote browser session -------------------------------------------------

let remoteAccountId = null;

async function openRemoteModal() {
  $('tt-remote-setup').classList.remove('hidden');
  $('tt-remote-live').classList.add('hidden');
  $('tt-remote-msg').textContent = '';
  $('tt-remote-modal').classList.remove('hidden');
  // Tell the user up-front if the server is missing a piece.
  try {
    const st = await api('/api/tiktok/session/status');
    const d = st.diagnostics || {};
    const missing = [];
    if (!d.xvfb) missing.push('Xvfb');
    if (!d.x11vnc) missing.push('x11vnc');
    if (!d.novnc_dir) missing.push('noVNC');
    if (!d.chromium) missing.push('Chromium');
    $('tt-remote-msg').textContent = missing.length
      ? `⚠ Missing on the server: ${missing.join(', ')} — run ./setup.sh again on the VPS.`
      : '';
  } catch (_) {}
}

async function startRemoteBrowser() {
  const name = $('tt-remote-name').value.trim();
  const msg = $('tt-remote-msg');
  if (!name) { msg.textContent = 'Give the account a name first.'; return; }
  msg.textContent = 'Starting the remote browser on the VPS…';
  try {
    const info = await api('/api/tiktok/session/start', {
      method: 'POST', body: JSON.stringify({ name }) });
    remoteAccountId = info.account_id;
    // noVNC connects back through the dashboard's authenticated WebSocket
    // bridge; host/port/encrypt must match the page we're served from.
    const secure = window.location.protocol === 'https:';
    const params = new URLSearchParams({
      autoconnect: '1', resize: 'remote', reconnect: '1',
      host: window.location.hostname,
      port: window.location.port || (secure ? '443' : '80'),
      encrypt: secure ? '1' : '0',
      path: 'api/vnc/ws',
      password: info.vnc_password,
    });
    const page = info.novnc_page || 'vnc.html';
    $('tt-remote-frame').src = `/novnc/${page}?${params.toString()}`;
    $('tt-vnc-info').textContent =
      `Host: ${window.location.hostname}   Port: ${info.vnc_port}   Password: ${info.vnc_password}\n` +
      `(the VNC port is bound to localhost — use an SSH tunnel: ` +
      `ssh -L ${info.vnc_port}:localhost:${info.vnc_port} user@your-vps)`;
    $('tt-remote-setup').classList.add('hidden');
    $('tt-remote-live').classList.remove('hidden');
  } catch (e) { msg.textContent = e.message; }
}

async function stopRemoteBrowser(done) {
  try { await api('/api/tiktok/session/stop', { method: 'POST' }); } catch (_) {}
  $('tt-remote-frame').src = '';
  $('tt-remote-modal').classList.add('hidden');
  if (done) {
    alert('Session saved ✓ — this account will publish using that logged-in browser.');
  }
  remoteAccountId = null;
  renderTtAccounts();
  loadMyChannels();
}

async function submitTtCookies() {
  const name = $('tt-cookie-name').value.trim();
  const file = $('tt-cookie-file').files[0];
  const msg = $('tt-cookie-msg');
  if (!name) { msg.textContent = 'Give the account a name.'; return; }
  if (!file) { msg.textContent = 'Choose your cookies.txt file.'; return; }
  msg.textContent = 'Uploading…';
  const fd = new FormData(); fd.append('name', name); fd.append('file', file);
  try {
    const res = await fetch('/api/tiktok/accounts/browser', { method: 'POST', body: fd });
    if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.detail || 'failed'); }
    const r = await res.json();
    msg.textContent = `Connected ✓ (${r.cookies} cookies)`;
    $('tt-cookie-name').value = ''; $('tt-cookie-file').value = '';
    renderTtAccounts();
    setTimeout(() => $('tt-cookie-modal').classList.add('hidden'), 800);
  } catch (e) { msg.textContent = e.message; }
}

// --- TikTok account detail + auto mode -------------------------------------

let currentTtId = null;

async function openTtAccount(id) {
  currentTtId = id;
  showMineView('tt');
  try {
    const sources = (await api('/api/channels')).channels;
    $('tt-sources').innerHTML = sources.length ? '' : '<span class="muted">Add source channels first.</span>';
    for (const sc of sources) {
      const row = document.createElement('label'); row.className = 'check-row';
      row.innerHTML = `<input type="checkbox" value="${sc.id}" id="ttsrc-${sc.id}" /> ${escapeHtml(sc.name || sc.url)}`;
      $('tt-sources').appendChild(row);
    }
  } catch (_) {}
  await loadCaptionStyles(); await loadMusicOptions();
  if (!languagesCache) { try { languagesCache = (await api('/api/languages')).languages; } catch (_) { languagesCache = { fr: 'French' }; } }
  const ls = $('tt-lang'); ls.innerHTML = '';
  for (const [c, n] of Object.entries(languagesCache)) { const o = document.createElement('option'); o.value = c; o.textContent = `${n} (${c})`; ls.appendChild(o); }
  ls.value = 'fr';
  await reloadTtAccount();
  stopChannelPoll(); channelPollTimer = setInterval(reloadTtAccount, 5000);
}

async function reloadTtAccount() {
  if (!currentTtId) return;
  let d; try { d = await api(`/api/tiktok/accounts/${currentTtId}`); } catch (_) { return; }
  $('ttaccount-name').textContent = '♪ ' + (d.title || 'TikTok');
  $('tt-stat-published').textContent = d.stats.published;
  $('tt-stat-pending').textContent = d.stats.pending;
  $('tt-stat-errors').textContent = d.stats.errors;
  $('tt-auto-enabled').checked = !!d.auto_enabled;
  const cfg = d.auto_config || {};
  if (cfg.target_lang) $('tt-lang').value = cfg.target_lang;
  if (cfg.cadence_mode) $('tt-cadence').value = cfg.cadence_mode;
  if (cfg.selection) $('tt-selection').value = cfg.selection;
  if (cfg.count) $('tt-count').value = cfg.count;
  if (cfg.post_mode) $('tt-postmode').value = cfg.post_mode;
  if (cfg.privacy) $('tt-privacy').value = cfg.privacy;
  if (cfg.times) $('tt-times').value = (cfg.times || []).join(',');
  if (cfg.caption_style !== undefined) $('tt-captions').value = cfg.caption_style || '';
  if (cfg.music_id !== undefined) $('tt-music').value = cfg.music_id || '';
  for (const scid of (cfg.source_channel_ids || [])) { const cb = $(`ttsrc-${scid}`); if (cb) cb.checked = true; }
  $('tt-manual-cadence').style.display = $('tt-cadence').value === 'manual' ? 'flex' : 'none';
  $('tt-count-wrap').style.display = $('tt-selection').value === 'number' ? 'block' : 'none';
  renderTtItems(d.items || []);
}

function renderTtItems(items) {
  const wrap = $('tt-items');
  if (!items.length) { wrap.innerHTML = '<div class="empty">Nothing queued yet.</div>'; return; }
  wrap.innerHTML = '';
  for (const it of items) {
    const el = document.createElement('div'); el.className = 'job';
    const cls = it.status === 'published' ? 'done' : (it.status === 'error' ? 'error' : 'active');
    const when = it.scheduled_at ? new Date(it.scheduled_at * 1000).toLocaleString() : '';
    const dubState = it.dub_status && it.dub_status !== 'done' ? ` · dub: ${STAGE_LABEL[it.dub_status] || it.dub_status}` : '';
    el.innerHTML = `<div class="job-info"><div class="job-title">${escapeHtml(it.title || 'Short')} <small class="muted">${(it.lang || '').toUpperCase()}</small></div>
      <div class="job-sub">${STAGE_LABEL[it.status] || it.status} · ${when}${dubState}${it.error ? ' · ' + escapeHtml(it.error) : ''}</div></div>
      <span class="badge ${cls}">${it.status}</span>`;
    if (it.dub_id) { el.style.cursor = 'pointer'; el.addEventListener('click', () => openDubModal(it.dub_id)); }
    wrap.appendChild(el);
  }
}

async function saveTtAuto() {
  const sources = Array.from($('tt-sources').querySelectorAll('input:checked')).map((c) => c.value);
  const cfg = {
    source_channel_ids: sources, target_lang: $('tt-lang').value,
    cadence_mode: $('tt-cadence').value,
    times: $('tt-times').value.split(',').map((t) => t.trim()).filter(Boolean),
    selection: $('tt-selection').value, count: parseInt($('tt-count').value, 10) || 30,
    post_mode: $('tt-postmode').value, privacy: $('tt-privacy').value,
    caption_style: $('tt-captions').value, music_id: $('tt-music').value,
  };
  const msg = $('tt-auto-msg');
  const enabled = $('tt-auto-enabled').checked;
  if (enabled && !sources.length) { msg.textContent = 'Tick at least one source channel first.'; return; }
  msg.textContent = 'Saving…';
  try {
    const r = await api(`/api/tiktok/accounts/${currentTtId}/auto`, {
      method: 'POST', body: JSON.stringify({ enabled, config: cfg }) });
    msg.textContent = enabled
      ? (r.started > 0 ? `Auto active ✓ — ${r.started} videos queued.` : 'Auto active ✓ — nothing new to queue.')
      : 'Saved ✓';
    reloadTtAccount();
  } catch (e) { msg.textContent = e.message; }
}

async function connectGoogle() {
  try {
    const { url } = await api('/api/youtube/auth-url');
    window.location.href = url;
  } catch (e) { alert(e.message); }
}
async function saveSettings() {
  const body = { gemini_api_key: $('set-gemini-key').value, gemini_model: $('set-gemini-model').value,
    whisper_model: $('set-whisper').value, tts_engine: $('set-tts').value,
    ngrok_authtoken: $('set-ngrok').value, new_password: $('set-password').value,
    default_dub_music: $('set-default-music').value,
    default_caption_style: $('set-default-caption').value,
    default_dub_captions: $('set-default-dub-captions').value,
    google_client_id: $('set-gclient').value, google_client_secret: $('set-gsecret').value,
    tiktok_client_key: $('set-ttkey').value, tiktok_client_secret: $('set-ttsecret').value };
  try {
    await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
    $('settings-msg').textContent = 'Saved ✓';
    setTimeout(() => $('settings-modal').classList.add('hidden'), 700);
  } catch (e) { $('settings-msg').textContent = e.message; }
}

// ===================== STOP / RESUME ALL QUEUES ============================

let systemPaused = false;

function renderStopButton() {
  const btn = $('btn-stop');
  if (systemPaused) {
    btn.textContent = '▶ Resume'; btn.classList.remove('danger'); btn.classList.add('primary');
  } else {
    btn.textContent = '⏹ Stop all'; btn.classList.add('danger'); btn.classList.remove('primary');
  }
}

async function loadSystemStatus() {
  try { systemPaused = (await api('/api/system/status')).paused; renderStopButton(); } catch (_) {}
}

async function toggleStop() {
  if (!systemPaused) {
    if (!confirm('Stop everything? This cancels all queued clips, dubs and pending uploads, and turns off Auto on every channel. The item currently rendering finishes.')) return;
    try {
      const r = await api('/api/system/stop-all', { method: 'POST' });
      systemPaused = true; renderStopButton();
      alert(`Stopped. Canceled: ${r.canceled_jobs} clips, ${r.canceled_dubs} dubs, ${r.canceled_publishes} uploads. Auto disabled on ${r.auto_disabled} channel(s).`);
      loadJobs();
    } catch (e) { alert(e.message); }
  } else {
    try { await api('/api/system/resume', { method: 'POST' }); systemPaused = false; renderStopButton(); } catch (e) { alert(e.message); }
  }
}

async function clearQueues() {
  if (!confirm('Delete ALL waiting, failed and canceled items (clips, dubs, uploads) and their files? Finished shorts and published videos are kept. This cannot be undone.')) return;
  try {
    const r = await api('/api/system/clear-queues', { method: 'POST' });
    systemPaused = true; renderStopButton();
    alert(`Cleared. Deleted: ${r.deleted_jobs} clips, ${r.deleted_dubs} dubs, ${r.deleted_publishes} uploads. System paused — press Resume when ready.`);
    loadJobs();
  } catch (e) { alert(e.message); }
}

// ===================== wire up =============================================

document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => switchMode(t.dataset.mode)));
document.querySelectorAll('.subtab').forEach((t) => t.addEventListener('click', () => switchSub(t.dataset.sub)));

$('btn-create').addEventListener('click', createJob);
$('btn-back').addEventListener('click', () => { closeStreams(); showClipView('list'); loadJobs(); });
$('btn-clear-failed').addEventListener('click', clearFailed);
$('btn-retry-job').addEventListener('click', retryJob);
$('btn-delete-job').addEventListener('click', deleteJob);
$('btn-upload-music').addEventListener('click', uploadMusic);
$('dub-retry').addEventListener('click', retryDub);
$('dub-delete').addEventListener('click', deleteDub);

$('btn-add-channel').addEventListener('click', addChannel);
$('btn-channel-back').addEventListener('click', () => { stopChannelPoll(); currentChannelId = null; showSourcesView('channels'); loadChannels(); });
$('btn-refresh-channel').addEventListener('click', refreshCurrentChannel);
$('btn-delete-channel').addEventListener('click', deleteCurrentChannel);

$('btn-add-mychannel').addEventListener('click', addMyChannel);
$('btn-mychannel-back').addEventListener('click', () => { stopChannelPoll(); currentMyChannelId = null; showMineView('list'); loadMyChannels(); });
$('btn-delete-mychannel').addEventListener('click', deleteCurrentMyChannel);
$('btn-ytchannel-back').addEventListener('click', () => { stopChannelPoll(); currentYtChannelId = null; showMineView('list'); loadMyChannels(); });
$('btn-save-auto').addEventListener('click', saveAuto);
$('yt-cadence').addEventListener('change', toggleCadenceFields);
$('yt-selection').addEventListener('change', toggleSelectionFields);
$('btn-connect-google').addEventListener('click', connectGoogle);
$('btn-connect-tiktok').addEventListener('click', openRemoteModal);
$('btn-connect-tiktok-api').addEventListener('click', connectTikTokApi);
$('btn-connect-tiktok-cookies').addEventListener('click', () => $('tt-cookie-modal').classList.remove('hidden'));
$('btn-start-remote').addEventListener('click', startRemoteBrowser);
$('btn-remote-done').addEventListener('click', () => stopRemoteBrowser(true));
$('btn-remote-stop').addEventListener('click', () => stopRemoteBrowser(false));
$('btn-close-ttremote').addEventListener('click', () => stopRemoteBrowser(false));
$('btn-close-ttcookie').addEventListener('click', () => $('tt-cookie-modal').classList.add('hidden'));
$('btn-submit-ttcookie').addEventListener('click', submitTtCookies);
$('btn-ttaccount-back').addEventListener('click', () => { stopChannelPoll(); currentTtId = null; showMineView('list'); loadMyChannels(); });
$('btn-save-tt-auto').addEventListener('click', saveTtAuto);
$('tt-cadence').addEventListener('change', () => { $('tt-manual-cadence').style.display = $('tt-cadence').value === 'manual' ? 'flex' : 'none'; });
$('tt-selection').addEventListener('change', () => { $('tt-count-wrap').style.display = $('tt-selection').value === 'number' ? 'block' : 'none'; });
$('btn-clear-logs').addEventListener('click', async () => {
  if (!confirm('Clear all activity logs?')) return;
  try { await api('/api/activity', { method: 'DELETE' }); } catch (_) {}
  loadLogs();
});
document.querySelectorAll('#log-filters .subtab').forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll('#log-filters .subtab').forEach((x) => x.classList.remove('active'));
  b.classList.add('active'); logFilter = b.dataset.kind || ''; loadLogs();
}));
$('btn-stop').addEventListener('click', toggleStop);
$('btn-clear-queues').addEventListener('click', clearQueues);

$('btn-close-lang').addEventListener('click', () => $('lang-modal').classList.add('hidden'));
$('btn-confirm-lang').addEventListener('click', confirmLang);
$('btn-close-dub').addEventListener('click', () => { closeDubStream(); $('dub-modal').classList.add('hidden'); });

$('btn-settings').addEventListener('click', openSettings);
$('btn-close-settings').addEventListener('click', () => $('settings-modal').classList.add('hidden'));
$('btn-save-settings').addEventListener('click', saveSettings);
$('btn-close-player').addEventListener('click', closePlayer);
$('btn-logout').addEventListener('click', async () => { await fetch('/api/logout', { method: 'POST' }); window.location.href = '/login'; });

[['player-modal', closePlayer],
 ['settings-modal', () => $('settings-modal').classList.add('hidden')],
 ['lang-modal', () => $('lang-modal').classList.add('hidden')],
 ['dub-modal', () => { closeDubStream(); $('dub-modal').classList.add('hidden'); }]].forEach(([id, fn]) => {
  $(id).addEventListener('click', (e) => { if (e.target.id === id) fn(); });
});

// Handle the OAuth return.
(function handleYtReturn() {
  const p = new URLSearchParams(window.location.search);
  if (p.get('yt') === 'connected') { alert('Google account connected ✓'); history.replaceState({}, '', '/'); }
  else if (p.get('yt') === 'error') { alert('Google connection failed. Check your client ID/secret and the redirect URI in Google Cloud.'); history.replaceState({}, '', '/'); }
  else if (p.get('tt') === 'connected') { alert('TikTok account connected ✓'); history.replaceState({}, '', '/'); }
  else if (p.get('tt') === 'error') { alert('TikTok connection failed. Check your client key/secret and the redirect URI in the TikTok developer portal.'); history.replaceState({}, '', '/'); }
})();

loadJobs();
loadMusicOptions();
loadCaptionStyles();
loadSystemStatus();
setInterval(() => {
  if (!$('screen-clip').classList.contains('hidden') && !$('view-list').classList.contains('hidden')) loadJobs();
}, 5000);
