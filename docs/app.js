/* DJ News Desk — 정적 프론트엔드. 빌드 없음, 의존성 없음. */

const HOME_PREVIEW = 5;          // 홈 대시보드에서 카테고리당 보여줄 개수
const BM_KEY = 'newsdesk.bookmarks';

const state = {
  data: null,          // 현재 보고 있는 스냅샷
  snapshots: [],       // 아카이브 목록
  tab: 'home',
  query: '',
  loading: true,
};

const $ = (sel) => document.querySelector(sel);
const view = $('#view');

/* ---------------------------------------------------------- 북마크 (localStorage) */

function readBookmarks() {
  try {
    return JSON.parse(localStorage.getItem(BM_KEY) || '[]');
  } catch { return []; }
}
function writeBookmarks(list) {
  try { localStorage.setItem(BM_KEY, JSON.stringify(list)); } catch { /* 사파리 시크릿 모드 등 */ }
}
function isSaved(url) {
  return readBookmarks().some((b) => b.url === url);
}
function toggleBookmark(article, catId) {
  const list = readBookmarks();
  const i = list.findIndex((b) => b.url === article.url);
  if (i >= 0) list.splice(i, 1);
  else list.unshift({
    url: article.url, title: article.title, source: article.source,
    published: article.published, cat: catId, saved_at: new Date().toISOString(),
  });
  writeBookmarks(list);
  return i < 0;
}

/* ---------------------------------------------------------- 유틸 */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function highlight(text, q) {
  const safe = escapeHtml(text);
  if (!q) return safe;
  const needle = escapeHtml(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return safe.replace(new RegExp(needle, 'gi'), (m) => `<mark>${m}</mark>`);
}

function timeAgo(iso) {
  const then = new Date(iso);
  if (isNaN(then)) return '';
  const min = Math.round((Date.now() - then.getTime()) / 60000);
  if (min < 1) return '방금';
  if (min < 60) return `${min}분 전`;
  if (min < 60 * 24) return `${Math.floor(min / 60)}시간 전`;
  const d = Math.floor(min / 1440);
  if (d < 7) return `${d}일 전`;
  return `${then.getMonth() + 1}.${then.getDate()}`;
}

function catName(id) {
  const c = (state.data?.categories || []).find((x) => x.id === id);
  return c ? c.name : id;
}

/* ---------------------------------------------------------- 카드 렌더 */

function cardHtml(a, catId, opts = {}) {
  const q = state.query;
  const saved = isSaved(a.url);
  const rel = a.related || [];
  const relTotal = a.related_total ?? rel.length;   // 링크는 12건까지만 싣지만 개수는 전체를 보여준다
  const relId = 'rel-' + Math.random().toString(36).slice(2, 9);

  const summary = a.summary
    ? `<p class="card-summary">${highlight(a.summary, q)}</p>` : '';

  const relBtn = relTotal
    ? `<button class="rel-toggle" data-rel="${relId}" data-total="${relTotal}">관련 ${relTotal}건 ▾</button>` : '';

  const relList = rel.length ? `
    <ul class="rel-list" id="${relId}" hidden>
      ${rel.map((r) => `
        <li><a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.title)}</a>
        <span class="rel-src">${escapeHtml(r.source)}</span></li>`).join('')}
      ${relTotal > rel.length
        ? `<li class="rel-more">그 밖에 ${relTotal - rel.length}개 매체가 더 보도했어요</li>` : ''}
    </ul>` : '';

  const chip = opts.showCat ? `<span class="cat-chip">${escapeHtml(catName(catId))}</span>` : '';

  return `
    <article class="card">
      <a class="card-title" href="${escapeHtml(a.url)}" target="_blank" rel="noopener">${highlight(a.title, q)}</a>
      ${summary}
      <div class="card-meta">
        ${chip}
        <span class="src">${escapeHtml(a.source || '')}</span>
        <span class="dot">·</span>
        <span>${timeAgo(a.published)}</span>
        ${relBtn ? '<span class="dot">·</span>' + relBtn : ''}
      </div>
      ${relList}
      <button class="star${saved ? ' on' : ''}" data-url="${escapeHtml(a.url)}" data-cat="${catId}"
              aria-label="나중에 보기">${saved ? '★' : '☆'}</button>
    </article>`;
}

/* ---------------------------------------------------------- 시세 스트립 */

function marketHtml() {
  const m = state.data?.market || [];
  if (!m.length) return '';

  let html = '<div class="market">';
  let lastGroup = null;
  for (const it of m) {
    if (lastGroup !== null && it.group !== lastGroup) html += '<div class="market-sep"></div>';
    lastGroup = it.group;
    // 국내 관행대로 오르면 빨강, 내리면 파랑
    const dir = it.ratio > 0 ? 'up' : it.ratio < 0 ? 'down' : 'flat';
    const sign = it.ratio > 0 ? '+' : '';
    html += `
      <a class="mcard ${dir}" href="${escapeHtml(it.url)}" target="_blank" rel="noopener">
        <span class="mname">${escapeHtml(it.name)}</span>
        <span class="mprice">${escapeHtml(it.price)}</span>
        <span class="mchg">${escapeHtml(it.diff)} (${sign}${it.ratio.toFixed(2)}%)</span>
      </a>`;
  }
  return html + '</div>';
}

/* ---------------------------------------------------------- 검색 필터 */

function matches(a, q) {
  if (!q) return true;
  const hay = [a.title, a.summary, a.source, ...(a.related || []).map((r) => r.title)]
    .join(' ').toLowerCase();
  return hay.includes(q.toLowerCase());
}

function itemsFor(catId) {
  const all = state.data?.articles?.[catId] || [];
  return state.query ? all.filter((a) => matches(a, state.query)) : all;
}

/* ---------------------------------------------------------- 화면 */

function renderTabs() {
  const cats = state.data?.categories || [];
  const bmCount = readBookmarks().length;
  const tabs = [
    { id: 'home', label: '홈' },
    ...cats.map((c) => ({ id: c.id, label: c.name, count: itemsFor(c.id).length })),
    { id: 'saved', label: '★ 저장', count: bmCount },
  ];
  $('#tabs').innerHTML = tabs.map((t) => `
    <button class="tab${state.tab === t.id ? ' active' : ''}" data-tab="${t.id}">
      ${escapeHtml(t.label)}${t.count != null ? `<span class="count">${t.count}</span>` : ''}
    </button>`).join('');
}

function renderHome() {
  const cats = state.data.categories;
  let html = '';
  let totalShown = 0;

  for (const c of cats) {
    const items = itemsFor(c.id);
    totalShown += items.length;
    if (state.query && !items.length) continue;
    html += `
      <section>
        ${c.id === 'economy' ? marketHtml() : ''}
        <div class="section-head">
          <h2 class="section-title">${c.emoji || ''} ${escapeHtml(c.name)}
            <span class="section-desc">${escapeHtml(c.desc || '')}</span></h2>
          <button class="more-btn" data-tab="${c.id}">전체 ${items.length} →</button>
        </div>
        ${items.length
          ? items.slice(0, HOME_PREVIEW).map((a) => cardHtml(a, c.id)).join('')
          : '<p class="empty" style="padding:20px">수집된 기사가 없어요</p>'}
      </section>`;
  }

  if (state.query && totalShown === 0) {
    html = `<p class="empty">‘${escapeHtml(state.query)}’와 맞는 기사가 없어요.<br>다른 키워드로 찾아보세요.</p>`;
  }
  if (state.data.slim) {
    html += `<p class="banner">지난 스냅샷은 가볍게 보관해서 카테고리당 상위 30건까지만 있고,
             관련기사 묶음 링크는 빠져 있어요.</p>`;
  }

  view.innerHTML = html;
}

function renderCategory(catId) {
  const c = state.data.categories.find((x) => x.id === catId);
  const items = itemsFor(catId);
  view.innerHTML = `
    ${catId === 'economy' ? marketHtml() : ''}
    <div class="section-head">
      <h2 class="section-title">${c.emoji || ''} ${escapeHtml(c.name)}
        <span class="section-desc">${escapeHtml(c.desc || '')}</span></h2>
      <span class="section-desc">${items.length}건</span>
    </div>
    ${items.length
      ? items.map((a) => cardHtml(a, catId)).join('')
      : `<p class="empty">${state.query ? '검색 결과가 없어요' : '수집된 기사가 없어요'}</p>`}`;
}

function renderSaved() {
  let list = readBookmarks();
  if (state.query) list = list.filter((a) => matches(a, state.query));
  view.innerHTML = `
    <div class="section-head">
      <h2 class="section-title">★ 나중에 보기</h2>
      <span class="section-desc">${list.length}건</span>
    </div>
    ${list.length
      ? list.map((a) => cardHtml(a, a.cat, { showCat: true })).join('')
      : `<p class="empty">저장한 기사가 없어요.<br>기사 오른쪽 ☆ 를 누르면 여기 모입니다.</p>`}`;
}

function render() {
  if (state.loading) {
    view.innerHTML = Array.from({ length: 6 })
      .map(() => '<div class="skeleton"><div></div><div></div><div></div></div>').join('');
    return;
  }
  if (!state.data) {
    view.innerHTML = '<p class="empty">데이터를 불러오지 못했어요.<br>↻ 를 눌러 다시 시도해 주세요.</p>';
    return;
  }
  renderTabs();
  if (state.tab === 'home') renderHome();
  else if (state.tab === 'saved') renderSaved();
  else renderCategory(state.tab);
  window.scrollTo({ top: 0 });
}

/* ---------------------------------------------------------- 데이터 로드 */

async function loadSnapshot(path, label) {
  state.loading = true;
  render();
  try {
    const res = await fetch(`${path}?t=${Date.now()}`);
    if (!res.ok) throw new Error(res.status);
    state.data = await res.json();
    const stats = state.data.stats || {};
    const total = Object.values(state.data.articles || {}).reduce((n, v) => n + v.length, 0);
    const raw = Object.values(stats).reduce((n, s) => n + (s.raw || 0), 0);
    $('#meta-line').textContent =
      `${label || state.data.generated_label} 기준 · ${total}건` + (raw ? ` (원본 ${raw}건에서 추림)` : '');
    $('#foot-note').textContent =
      `출처: 구글뉴스 + 언론사 RSS${state.data.naver_enabled ? ' + 네이버' : ''} · 하루 2회(07:00 / 19:00) 자동 수집`;
  } catch (e) {
    state.data = null;
    $('#meta-line').textContent = '데이터를 불러오지 못했어요';
  }
  state.loading = false;
  render();
}

async function loadArchiveIndex() {
  try {
    const res = await fetch(`data/index.json?t=${Date.now()}`);
    const j = await res.json();
    state.snapshots = j.snapshots || [];
  } catch { state.snapshots = []; }

  const sel = $('#archive-select');
  sel.innerHTML = state.snapshots.length
    ? state.snapshots.map((s) => `<option value="${s.file}">${s.label}</option>`).join('')
    : '<option>저장된 스냅샷이 없어요</option>';
}

/* ---------------------------------------------------------- 이벤트 */

document.addEventListener('click', (e) => {
  const tabBtn = e.target.closest('[data-tab]');
  if (tabBtn) {
    state.tab = tabBtn.dataset.tab;
    render();
    return;
  }

  const star = e.target.closest('.star');
  if (star) {
    const catId = star.dataset.cat;
    const url = star.dataset.url;
    const article = (state.data?.articles?.[catId] || []).find((a) => a.url === url)
      || readBookmarks().find((b) => b.url === url);
    if (!article) return;
    const nowSaved = toggleBookmark(article, catId);
    star.classList.toggle('on', nowSaved);
    star.textContent = nowSaved ? '★' : '☆';
    if (state.tab === 'saved') render(); else renderTabs();
    return;
  }

  const relBtn = e.target.closest('[data-rel]');
  if (relBtn) {
    const ul = document.getElementById(relBtn.dataset.rel);
    const open = ul.hidden;
    ul.hidden = !open;
    relBtn.textContent = `관련 ${relBtn.dataset.total}건 ${open ? '▴' : '▾'}`;
  }
});

$('#btn-search').addEventListener('click', () => {
  const bar = $('#search-bar');
  bar.hidden = !bar.hidden;
  $('#btn-search').classList.toggle('on', !bar.hidden);
  $('#archive-bar').hidden = true;
  $('#btn-archive').classList.remove('on');
  if (!bar.hidden) $('#search-input').focus();
});

let searchTimer;
$('#search-input').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.query = e.target.value.trim(); render(); }, 180);
});

$('#search-clear').addEventListener('click', () => {
  $('#search-input').value = '';
  state.query = '';
  render();
});

$('#btn-archive').addEventListener('click', () => {
  const bar = $('#archive-bar');
  bar.hidden = !bar.hidden;
  $('#btn-archive').classList.toggle('on', !bar.hidden);
  $('#search-bar').hidden = true;
  $('#btn-search').classList.remove('on');
});

$('#archive-select').addEventListener('change', (e) => {
  const snap = state.snapshots.find((s) => s.file === e.target.value);
  if (snap) loadSnapshot(`data/archive/${snap.file}`, snap.label);
});

$('#archive-latest').addEventListener('click', () => {
  $('#archive-select').selectedIndex = 0;
  loadSnapshot('data/latest.json');
});

$('#btn-refresh').addEventListener('click', async (e) => {
  e.currentTarget.classList.add('spin');
  await loadArchiveIndex();
  await loadSnapshot('data/latest.json');
  e.currentTarget.classList.remove('spin');
});

/* ---------------------------------------------------------- 시작 */

loadArchiveIndex();
loadSnapshot('data/latest.json');
