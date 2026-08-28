/* DJ News Desk — 정적 프론트엔드. 빌드 없음, 의존성 없음. */

const HOME_PREVIEW = 5;          // 홈 대시보드에서 카테고리당 보여줄 개수
const BM_KEY = 'newsdesk.bookmarks';
const READ_KEY = 'newsdesk.read';
const READ_MAX = 800;
const PAGE_SIZE = 60;      // 카테고리 탭에서 한 번에 그리는 기사 수

const state = {
  data: null,          // 홈/메타 (latest.json)
  store: {},           // 카테고리별 누적 기사 (cat/<id>.json). 필요할 때 받아서 캐시한다
  storeLoading: {},
  snapshots: [],       // 아카이브 목록
  tab: 'home',
  query: '',
  page: PAGE_SIZE,
  dealsOpen: false,
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

/* ---------------------------------------------------------- 읽은 기사 */

let readSet = null;

function getRead() {
  if (readSet) return readSet;
  try { readSet = new Set(JSON.parse(localStorage.getItem(READ_KEY) || '[]')); }
  catch { readSet = new Set(); }
  return readSet;
}

function markRead(url) {
  const set = getRead();
  if (set.has(url)) return;
  set.add(url);
  // 무한정 쌓이지 않게 오래된 것부터 버린다
  let arr = [...set];
  if (arr.length > READ_MAX) { arr = arr.slice(-READ_MAX); readSet = new Set(arr); }
  try { localStorage.setItem(READ_KEY, JSON.stringify(arr)); } catch { /* 저장 불가 환경 */ }
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

  // 기계번역은 틀릴 수 있으니 원문을 버리지 않고 함께 보여준다
  const headline = a.title_ko || a.title;
  const original = a.title_ko
    ? `<p class="card-original">${highlight(a.title, q)}</p>` : '';
  const body = a.summary_ko || a.summary;
  const summary = body
    ? `<p class="card-summary">${highlight(body, q)}</p>` : '';

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
  const intl = a.lang === 'en'
    ? `<span class="intl-chip">외신</span>${a.title_ko ? '<span class="tr-chip" title="기계번역입니다. 원문을 함께 확인하세요">번역</span>' : ''}`
    : '';

  return `
    <article class="card${getRead().has(a.url) ? ' read' : ''}">
      <a class="card-title" href="${escapeHtml(a.url)}" target="_blank" rel="noopener">${highlight(headline, q)}</a>
      ${original}
      ${summary}
      <div class="card-meta">
        ${chip}${intl}
        <span class="src">${escapeHtml(a.source || '')}</span>
        <span class="dot">·</span>
        <span>${timeAgo(a.published)}</span>
        <span class="dot">·</span>
        <a class="read-link" href="${escapeHtml(a.url)}" target="_blank" rel="noopener">기사 보기 ↗</a>
        ${relBtn ? '<span class="dot">·</span>' + relBtn : ''}
      </div>
      ${relList}
      <button class="star${saved ? ' on' : ''}" data-url="${escapeHtml(a.url)}" data-cat="${catId}"
              aria-label="나중에 보기">${saved ? '★' : '☆'}</button>
    </article>`;
}

/* ---------------------------------------------------------- 딜 테이블 */

const NA = '<span class="na">비공개</span>';
const DEALS_PREVIEW = 8;   // 표가 화면을 다 잡아먹지 않게 처음엔 이만큼만 보여준다

function cell(v) {
  return v ? escapeHtml(v) : NA;
}

function dealsHtml() {
  const rows = state.data?.deals || [];
  if (!rows.length) return '';

  const shown = state.dealsOpen ? rows : rows.slice(0, DEALS_PREVIEW);
  const body = shown.map((r) => `
    <tr>
      <td class="c-date">${escapeHtml((r.date || '').slice(2).replace(/-/g, '.'))}</td>
      <td>${r.investors && r.investors.length ? escapeHtml(r.investors.join('·')) : NA}</td>
      <td class="c-target">${r.target
        ? escapeHtml(r.target)
        : (r.headline ? `<span class="c-head" title="${escapeHtml(r.headline)}">${escapeHtml(r.headline)}</span>` : NA)}</td>
      <td>${cell(r.sector)}</td>
      <td>${cell(r.round)}</td>
      <td class="c-num">${cell(r.ev)}</td>
      <td class="c-num">${cell(r.amount)}</td>
      <td class="c-num">${cell(r.stake)}</td>
      <td class="c-src">
        <a href="${escapeHtml(r.url)}" target="_blank" rel="noopener"
           title="${escapeHtml(r.title)}">${escapeHtml(r.source)} ↗</a>
      </td>
    </tr>`).join('');

  return `
    <section class="deals">
      <div class="section-head">
        <h2 class="section-title">💰 최근 딜
          <span class="section-desc">투자 유치 · 인수 · 매각</span></h2>
        <span class="section-desc">${rows.length}건</span>
      </div>
      <div class="table-scroll">
        <table class="deal-table">
          <thead>
            <tr>
              <th>일자</th><th>투자자</th><th>Target</th><th>분야</th><th>라운드</th>
              <th>기업가치(EV)</th><th>투자금액</th><th>투자 후 지분율</th><th>출처</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
      ${rows.length > DEALS_PREVIEW ? `
        <button class="deals-more" id="deals-more">
          ${state.dealsOpen ? '접기 ▴' : `딜 ${rows.length - DEALS_PREVIEW}건 더보기 ▾`}
        </button>` : ''}
      <p class="deals-note">
        기사에서 확실히 읽히는 값만 채웁니다. 원문에 없으면 추정하지 않고 <b>비공개</b>로 둡니다.
        ${state.data?.dart_enabled
          ? '상장사 딜은 DART 공시에서 가져와 금액·지분율이 정확합니다.'
          : 'DART 인증키를 넣으면 상장사 딜의 투자금액·지분율이 공시 기준으로 정확히 채워집니다.'}
      </p>
    </section>`;
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
  const hay = [a.title, a.title_ko, a.summary, a.source, ...(a.related || []).map((r) => r.title)]
    .join(' ').toLowerCase();
  return hay.includes(q.toLowerCase());
}

function itemsFor(catId) {
  // 누적 파일을 받아왔으면 그쪽이 전체다. 아직이면 홈에 실린 일부만 보여준다.
  const all = state.store[catId] || state.data?.articles?.[catId] || [];
  return state.query ? all.filter((a) => matches(a, state.query)) : all;
}

function totalFor(catId) {
  return state.store[catId]?.length ?? state.data?.totals?.[catId] ?? 0;
}

async function loadCategory(catId) {
  if (state.store[catId] || state.storeLoading[catId] || state.data?.slim) return;
  state.storeLoading[catId] = true;
  try {
    const res = await fetch(`data/cat/${catId}.json?t=${state.data?.generated_at || ''}`);
    if (res.ok) state.store[catId] = (await res.json()).articles || [];
  } catch { /* 못 받으면 홈에 실린 일부만 보여준다 */ }
  state.storeLoading[catId] = false;
  render();
}

// 첫 화면을 그린 뒤, 검색이 전체를 훑을 수 있도록 나머지 카테고리를 뒤에서 채운다
async function preloadCategories() {
  for (const c of state.data?.categories || []) {
    await loadCategory(c.id);
  }
}

/* ---------------------------------------------------------- 화면 */

function renderTabs() {
  const cats = state.data?.categories || [];
  const bmCount = readBookmarks().length;
  const tabs = [
    { id: 'home', label: '홈' },
    ...cats.map((c) => ({ id: c.id, label: c.name, count: state.query ? itemsFor(c.id).length : totalFor(c.id) })),
    { id: 'sources', label: '📡 소스' },
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
        ${c.id === 'mna' ? dealsHtml() : ''}
        <div class="section-head">
          <h2 class="section-title">${c.emoji || ''} ${escapeHtml(c.name)}
            <span class="section-desc">${escapeHtml(c.desc || '')}</span></h2>
          <button class="more-btn" data-tab="${c.id}">전체 ${state.query ? items.length : totalFor(c.id)} →</button>
        </div>
        ${items.length
          ? items.slice(0, HOME_PREVIEW).map((a) => cardHtml(a, c.id)).join('')
          : '<p class="empty" style="padding:20px">수집된 기사가 없어요</p>'}
      </section>`;
  }

  if (state.query && totalShown === 0) {
    html = `<p class="empty">‘${escapeHtml(state.query)}’와 맞는 기사가 없어요.<br>다른 키워드로 찾아보세요.</p>`;
  }
  html += '<p class="swipe-hint">← 좌우로 밀어서 탭을 넘길 수 있어요 →</p>';
  if (state.data.slim) {
    html += `<p class="banner">지난 스냅샷은 가볍게 보관해서 카테고리당 상위 30건까지만 있고,
             관련기사 묶음 링크는 빠져 있어요.</p>`;
  }

  view.innerHTML = html;
}

function renderCategory(catId) {
  const c = state.data.categories.find((x) => x.id === catId);
  const items = itemsFor(catId);
  const shown = Math.min(state.page, items.length);
  const loading = state.storeLoading[catId];

  view.innerHTML = `
    ${catId === 'economy' ? marketHtml() : ''}
    ${catId === 'mna' ? dealsHtml() : ''}
    <div class="section-head">
      <h2 class="section-title">${c.emoji || ''} ${escapeHtml(c.name)}
        <span class="section-desc">${escapeHtml(c.desc || '')}</span></h2>
      <span class="section-desc">${items.length}건${loading ? ' 불러오는 중…' : ''}</span>
    </div>
    ${items.length
      ? items.slice(0, shown).map((a) => cardHtml(a, catId)).join('')
      : `<p class="empty">${state.query ? '검색 결과가 없어요'
          : loading ? '불러오는 중…' : '수집된 기사가 없어요'}</p>`}
    ${items.length > shown
      ? `<button class="deals-more" id="more-articles">${items.length - shown}건 더보기 ▾</button>` : ''}`;

  loadCategory(catId);
}

function renderSources() {
  const groups = state.data?.sources || [];
  const kinds = {};
  for (const g of groups) for (const e of g.entries) kinds[e.kind] = (kinds[e.kind] || 0) + 1;

  const summary = Object.entries(kinds)
    .map(([k, n]) => `<span class="src-chip">${escapeHtml(k)} ${n}</span>`).join('');

  const body = groups.map((g) => `
    <section class="src-group">
      <div class="section-head">
        <h3 class="section-title">${g.emoji || ''} ${escapeHtml(g.name)}</h3>
        <span class="section-desc">보관 ${g.stored || 0}건 · 이번 수집 ${g.raw || 0}건</span>
      </div>
      <ul class="src-list">
        ${g.entries.map((e) => `
          <li>
            <span class="src-kind k-${e.kind.includes('외신') ? 'intl' : e.kind.includes('구글') ? 'google'
              : e.kind.includes('네이버') ? 'naver' : 'press'}">${escapeHtml(e.kind)}</span>
            <span class="src-name">${e.url
              ? `<a href="${escapeHtml(e.url)}" target="_blank" rel="noopener">${escapeHtml(e.name)}</a>`
              : escapeHtml(e.name)}</span>
            ${e.detail ? `<span class="src-detail">${escapeHtml(e.detail)}</span>` : ''}
          </li>`).join('')}
      </ul>
    </section>`).join('');

  const deals = state.data?.dart_enabled
    ? `<li><span class="src-kind k-dart">공시</span>
         <span class="src-name"><a href="https://opendart.fss.or.kr" target="_blank" rel="noopener">DART 전자공시</a></span>
         <span class="src-detail">타법인주식 양수·양도 / 영업 양수·양도 / 합병 / 주식교환 — 최근 88일</span></li>`
    : `<li><span class="src-kind k-dart">공시</span><span class="src-name">DART</span>
         <span class="src-detail">인증키 미설정</span></li>`;

  view.innerHTML = `
    <div class="section-head">
      <h2 class="section-title">📡 수집 소스
        <span class="section-desc">지금 어디서 가져오고 있는지</span></h2>
    </div>
    <div class="src-chips">${summary}</div>

    <section class="src-group">
      <div class="section-head"><h3 class="section-title">💰 딜 · 시세</h3></div>
      <ul class="src-list">
        ${deals}
        <li><span class="src-kind k-market">시세</span>
          <span class="src-name"><a href="https://m.stock.naver.com" target="_blank" rel="noopener">네이버 금융</a></span>
          <span class="src-detail">코스피·코스닥·다우·S&amp;P·나스닥·원달러·삼성전자·SK하이닉스</span></li>
      </ul>
    </section>

    ${body}

    <p class="banner">
      소스는 저장소의 <b>config.json</b> 하나로 관리합니다. 여기에 피드나 키워드를 추가하면
      수집기와 이 화면이 함께 바뀝니다. 기사 본문은 저장하지 않고 제목과 요약 일부만 인용하며
      항상 원문으로 링크합니다.
    </p>`;
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
  else if (state.tab === 'sources') renderSources();
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
    // 화면에 보이는 수가 아니라 실제로 쌓여 있는 총계를 보여준다
    const totals = state.data.totals || {};
    const stored = Object.values(totals).reduce((n, v) => n + v, 0)
      || Object.values(state.data.articles || {}).reduce((n, v) => n + v.length, 0);
    const added = Object.values(state.data.stats || {}).reduce((n, s) => n + (s.added || 0), 0);
    $('#meta-line').textContent =
      `${label || state.data.generated_label} 갱신 · 누적 ${stored.toLocaleString()}건`
      + (added ? ` (이번에 ${added}건 추가)` : '');
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
  // 기사를 열면 읽은 것으로 표시한다 (같은 탭/새 탭 어느 쪽이든)
  const link = e.target.closest('.card-title, .rel-list a');
  if (link) {
    markRead(link.getAttribute('href'));
    link.closest('.card')?.classList.add('read');
  }

  const tabBtn = e.target.closest('[data-tab]');
  if (tabBtn) {
    state.tab = tabBtn.dataset.tab;
    state.page = PAGE_SIZE;
    render();
    return;
  }

  if (e.target.closest('#more-articles')) {
    state.page += PAGE_SIZE;
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

  if (e.target.closest('#deals-more')) {
    state.dealsOpen = !state.dealsOpen;
    render();
    document.querySelector('.deals')?.scrollIntoView({ block: 'start' });
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

let searchTimer;
$('#search-input').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  $('#search-clear').hidden = !e.target.value;
  searchTimer = setTimeout(() => { state.query = e.target.value.trim(); state.page = PAGE_SIZE; render(); }, 180);
});

$('#search-clear').addEventListener('click', () => {
  $('#search-input').value = '';
  $('#search-clear').hidden = true;
  state.query = '';
  render();
});

$('#btn-archive').addEventListener('click', () => {
  const bar = $('#archive-bar');
  bar.hidden = !bar.hidden;
  $('#btn-archive').classList.toggle('on', !bar.hidden);
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
  const icon = e.currentTarget.querySelector('.ico');   // 버튼 전체가 돌면 어지럽다
  icon?.classList.add('spin');
  await loadArchiveIndex();
  await loadSnapshot('data/latest.json');
  state.store = {};
  await preloadCategories();
  icon?.classList.remove('spin');
});

/* ---------------------------------------------------------- 스와이프로 탭 전환 */

function tabOrder() {
  return ['home', ...(state.data?.categories || []).map((c) => c.id), 'saved', 'sources'];
}

function moveTab(step) {
  const order = tabOrder();
  const i = order.indexOf(state.tab);
  const next = order[i + step];
  if (!next) return;
  state.tab = next;
  state.page = PAGE_SIZE;
  render();
  // 이동한 탭이 화면 밖이면 탭바를 따라 스크롤시킨다
  document.querySelector(`.tab[data-tab="${next}"]`)
    ?.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
}

(function enableSwipe() {
  let x0 = null, y0 = null, t0 = 0;
  const main = document.getElementById('view');

  main.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1) { x0 = null; return; }
    x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; t0 = Date.now();
  }, { passive: true });

  main.addEventListener('touchend', (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0;
    const dy = e.changedTouches[0].clientY - y0;
    x0 = null;
    // 세로 스크롤·시세 스트립 가로 스크롤과 부딪히지 않게 조건을 좁게 잡는다
    if (Date.now() - t0 > 600) return;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 2) return;
    if (e.target.closest('.market, .tabs, .rel-list')) return;
    moveTab(dx < 0 ? 1 : -1);
  }, { passive: true });
})();

// 데스크톱에서는 좌우 화살표 키로도 넘긴다
document.addEventListener('keydown', (e) => {
  if (e.target.matches('input, select, textarea')) return;
  if (e.key === 'ArrowRight') moveTab(1);
  if (e.key === 'ArrowLeft') moveTab(-1);
});

/* ---------------------------------------------------------- 시작 */

loadArchiveIndex();
loadSnapshot('data/latest.json').then(preloadCategories);
