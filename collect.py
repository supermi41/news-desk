#!/usr/bin/env python3
"""
DJ News Desk - 뉴스 수집기

무료 소스만 사용한다.
  - Google News RSS (키 불필요)
  - Naver 검색 API (NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 있을 때만)
  - 언론사 RSS (config.json의 publisher_rss.enabled = true 일 때만)

표준 라이브러리만 쓴다. pip install 필요 없음.

사용:
    python3 collect.py                 # 수집 → docs/data/ 갱신
    python3 collect.py --dry-run       # 파일 안 쓰고 결과만 출력
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
DATA_DIR = os.path.join(ROOT, "docs", "data")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
REQUEST_GAP = 0.4        # 초. 소스에 예의를 지킨다.
RELATED_LINK_LIMIT = 12  # 관련기사 링크 저장 상한 (용량 관리)
ARCHIVE_PER_CATEGORY = 30  # 아카이브 스냅샷에 남길 카테고리당 기사 수


# ---------------------------------------------------------------- 공통 유틸

def load_dotenv():
    """
    프로젝트 폴더의 .env 를 읽어 환경변수로 올린다. 외부 패키지를 쓰지 않는다.
    이미 환경변수에 값이 있으면 덮어쓰지 않는다 (GitHub Actions의 Secrets 우선).
    """
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and not os.environ.get(key):
                os.environ[key] = val


def log(msg):
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


_fetch_cache = {}


def fetch(url, headers=None, timeout=20, cache=False):
    """cache=True면 같은 URL을 한 번만 내려받는다. 언론사 피드를 여러 카테고리가 공유한다."""
    if cache and url in _fetch_cache:
        return _fetch_cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read()
    if cache:
        _fetch_cache[url] = body
    return body


def clean_text(s):
    """HTML 태그 제거 + 엔티티 복원 + 공백 정리."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = s.replace("​", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_date(raw):
    """RFC822 날짜 문자열 → KST datetime. 실패하면 현재 시각."""
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(KST)
        except Exception:
            pass
    return datetime.now(KST)


# ------------------------------------------------------- 제목 정규화 / 중복 제거

# 구글뉴스 제목 끝에 붙는 " - 언론사" 꼬리표
TITLE_SOURCE_TAIL = re.compile(r"\s+[-–—|]\s+[^\-–—|]{2,20}$")
# 기사 제목 앞머리의 대괄호 태그: [단독], [속보], [Why], (종합) 등
TITLE_BRACKET_HEAD = re.compile(r"^\s*[\[\(【][^\]\)】]{1,12}[\]\)】]\s*")


def strip_source_tail(title):
    return TITLE_SOURCE_TAIL.sub("", title).strip()


def normalize_title(title):
    t = title
    while True:
        stripped = TITLE_BRACKET_HEAD.sub("", t)
        if stripped == t:
            break
        t = stripped
    t = re.sub(r"[^\w가-힣]+", "", t)
    return t.lower()


def shingles(text, n):
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


# 언론 보도가 아닌 소스는 아예 수집에서 뺀다.
BLOCKED_HOSTS = (
    "blog.naver.com", "post.naver.com", "cafe.naver.com", "in.naver.com",
    "tistory.com", "brunch.co.kr", "blog.daum.net", "cafe.daum.net",
    "youtube.com", "youtu.be", "dcinside.com", "fmkorea.com", "clien.net",
    "medium.com", "velog.io", "wordpress.com", "blogspot.com",
)
BLOCKED_SOURCES = {
    "naver blog", "네이버 블로그", "naver post", "네이버 포스트", "naver cafe",
    "네이버 카페", "tistory", "티스토리", "브런치", "brunch", "youtube", "유튜브",
}


def is_blocked(url, source):
    host = urllib.parse.urlparse(url).netloc.lower()
    if any(b in host for b in BLOCKED_HOSTS):
        return True
    return (source or "").strip().lower() in BLOCKED_SOURCES


# 기사 원문을 다시 실어주는 포털은 출처로 쓰지 않는다. 묶인 기사 중 실제 언론사를 대표로 올린다.
AGGREGATORS = {
    "v.daum.net", "daum.net", "다음", "news.nate.com", "네이트", "nate.com",
    "zum.com", "ZUM", "msn.com", "MSN", "n.news.naver.com", "네이버",
    "news.naver.com", "media.naver.com", "출처 미상",
}


def is_aggregator(source):
    return not source or source.strip() in AGGREGATORS


def cluster_articles(articles, threshold, shingle_size):
    """
    비슷한 제목의 기사를 하나로 묶는다.
    대표 기사 1건 + related[] 형태로 반환.
    O(n^2)이지만 카테고리당 수백 건이라 충분히 빠르다.
    """
    clusters = []  # [{"rep":..., "related":[...], "members_sh":[set,...]}]
    for art in articles:
        key = normalize_title(art["title"])
        sh = shingles(key, shingle_size)
        hit = None
        for c in clusters:
            if key and key in c["keys"]:
                hit = c
                break
            # 대표뿐 아니라 이미 묶인 기사 전부와 비교한다.
            # 같은 사건이라도 제목 표현이 갈리므로 연쇄로 이어붙어야 잘 묶인다.
            if any(jaccard(sh, m) >= threshold for m in c["members_sh"]):
                hit = c
                break
        if hit:
            hit["related"].append(art)
            hit["members_sh"].append(sh)
            hit["keys"].add(key)
        else:
            clusters.append({"rep": art, "keys": {key}, "members_sh": [sh], "related": []})

    # greedy 배정은 같은 사건을 두 클러스터로 가르기 쉽다.
    # 클러스터끼리 다시 비교해서 겹치면 합친다.
    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            for j in range(len(clusters) - 1, i, -1):
                a, b = clusters[i], clusters[j]
                if any(jaccard(x, y) >= threshold for x in a["members_sh"] for y in b["members_sh"]):
                    a["related"].append(b["rep"])
                    a["related"].extend(b["related"])
                    a["members_sh"].extend(b["members_sh"])
                    a["keys"] |= b["keys"]
                    clusters.pop(j)
                    merged = True

    out = []
    for c in clusters:
        members = [c["rep"]] + c["related"]
        # 대표는 포털이 아닌 언론사 중 가장 최신 기사로 고른다
        real = [m for m in members if not is_aggregator(m["source"])]
        # 요약(스니펫)이 붙은 기사를 대표로 올려야 카드가 풍성해진다
        pick = next((m for m in real if m.get("summary")), None) \
            or (real[0] if real else members[0])
        c["related"] = [m for m in members if m is not pick]
        rep = dict(pick)
        # 대표 기사에 요약 스니펫이 없으면, 묶인 기사 중 있는 걸 끌어온다
        if not rep.get("summary"):
            for r in c["related"]:
                if r.get("summary"):
                    rep["summary"] = r["summary"]
                    break
        rest = [m for m in members if m is not pick]
        rest.sort(key=lambda m: m["published"], reverse=True)
        rep["related_total"] = len(rest)
        # URL이 기사당 평균 177자라 용량을 많이 먹는다. 링크는 최신 12건까지만 싣는다.
        rep["related"] = [
            {"title": r["title"], "url": r["url"], "source": r["source"], "published": r["published"]}
            for r in rest[:RELATED_LINK_LIMIT]
        ]
        out.append(rep)
    return out


# ------------------------------------------------------------------- 수집기

def parse_rss(xml_bytes, default_source=""):
    """RSS 2.0 <item> 목록을 표준 dict로 변환."""
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log(f"  ! XML 파싱 실패: {e}")
        return items

    for item in root.iter("item"):
        title = clean_text(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        src_el = item.find("source")
        source = clean_text(src_el.text) if src_el is not None and src_el.text else default_source
        if not source:
            source = strip_source_tail_to_name(title)
        desc = clean_text(item.findtext("description") or "")
        # 구글뉴스 description은 관련기사 링크 목록이라 요약으로 쓸 수 없다.
        if desc.startswith(title[:20]) or "news.google.com" in (item.findtext("description") or ""):
            desc = ""
        items.append({
            "title": strip_source_tail(title),
            "url": link,
            "source": prettify_source(source),
            "summary": desc[:220],
            "published": parse_date(item.findtext("pubDate")).isoformat(),
        })
    return items


def prettify_source(s):
    """출처가 호스트명으로 넘어오면 한글 언론사명으로 바꾼다."""
    s = (s or "").strip()
    if not s or " " in s or "." not in s:
        return s
    host = s.lower().replace("www.", "")
    if host in NAVER_HOST_NAMES:
        return NAVER_HOST_NAMES[host]
    trimmed = host.split(".", 1)[1]          # news.sbs.co.kr -> sbs.co.kr
    return NAVER_HOST_NAMES.get(trimmed, s)


def strip_source_tail_to_name(title):
    m = TITLE_SOURCE_TAIL.search(title)
    return m.group(0).lstrip(" -–—|").strip() if m else "출처 미상"


def google_topic(topic):
    url = f"https://news.google.com/rss/headlines/section/topic/{topic}?hl=ko&gl=KR&ceid=KR:ko"
    log(f"  구글 토픽: {topic}")
    return parse_rss(fetch(url))


def google_search(query):
    q = urllib.parse.quote(f"{query} when:2d")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    log(f"  구글 검색: {query}")
    return parse_rss(fetch(url))


def naver_search(query, cid, secret, display=40):
    q = urllib.parse.quote(query)
    url = f"https://openapi.naver.com/v1/search/news.json?query={q}&display={display}&sort=date"
    log(f"  네이버 검색: {query}")
    raw = fetch(url, headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret})
    payload = json.loads(raw)
    items = []
    for it in payload.get("items", []):
        title = clean_text(it.get("title", ""))
        link = it.get("originallink") or it.get("link") or ""
        if not title or not link:
            continue
        host = urllib.parse.urlparse(link).netloc.replace("www.", "")
        items.append({
            "title": title,
            "url": link,
            "source": NAVER_HOST_NAMES.get(host, host),
            "summary": clean_text(it.get("description", ""))[:220],
            "published": parse_date(it.get("pubDate")).isoformat(),
        })
    return items


# 자주 나오는 언론사 도메인은 한글 이름으로 보여준다.
NAVER_HOST_NAMES = {
    "hankyung.com": "한국경제", "mk.co.kr": "매일경제", "sedaily.com": "서울경제",
    "edaily.co.kr": "이데일리", "mt.co.kr": "머니투데이", "fnnews.com": "파이낸셜뉴스",
    "yna.co.kr": "연합뉴스", "news1.kr": "뉴스1", "newsis.com": "뉴시스",
    "chosun.com": "조선일보", "biz.chosun.com": "조선비즈", "donga.com": "동아일보",
    "joongang.co.kr": "중앙일보", "hani.co.kr": "한겨레", "khan.co.kr": "경향신문",
    "seoul.co.kr": "서울신문", "kmib.co.kr": "국민일보", "segye.com": "세계일보",
    "asiae.co.kr": "아시아경제", "heraldcorp.com": "헤럴드경제", "etnews.com": "전자신문",
    "thebell.co.kr": "더벨", "dealsite.co.kr": "딜사이트", "ajunews.com": "아주경제",
    "v.daum.net": "다음뉴스", "news.nate.com": "네이트뉴스", "ytn.co.kr": "YTN", "sbs.co.kr": "SBS", "kbs.co.kr": "KBS", "imnews.imbc.com": "MBC",
    "foodnews.co.kr": "식품음료신문", "thescoop.co.kr": "더스쿠프",
}


def publisher_feed(feed):
    """언론사 RSS 하나를 읽고, keywords가 있으면 제목·요약에 걸리는 것만 남긴다."""
    items = parse_rss(fetch(feed["url"], cache=True), feed["name"])
    kws = feed.get("keywords")
    if kws:
        items = [
            it for it in items
            if any(k in it["title"] or k in it["summary"] for k in kws)
        ]
    return items


# --------------------------------------------------------- 시세 (네이버 금융)
# 1세대 대시보드에서 두영이 명시적으로 요청했던 구성: 국장 / 미장 / 환율 / 반도체주.
# 네이버 금융 모바일 엔드포인트라 키가 필요 없다. 비공식 API이므로 실패해도 뉴스 수집은 계속된다.

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

MARKET_ITEMS = [
    ("국장", "코스피", "https://m.stock.naver.com/api/index/KOSPI/basic",
     "https://m.stock.naver.com/domestic/index/KOSPI/total"),
    ("국장", "코스닥", "https://m.stock.naver.com/api/index/KOSDAQ/basic",
     "https://m.stock.naver.com/domestic/index/KOSDAQ/total"),
    ("미장", "다우", "https://api.stock.naver.com/index/.DJI/basic",
     "https://m.stock.naver.com/worldstock/index/.DJI/total"),
    ("미장", "S&P 500", "https://api.stock.naver.com/index/.INX/basic",
     "https://m.stock.naver.com/worldstock/index/.INX/total"),
    ("미장", "나스닥", "https://api.stock.naver.com/index/.IXIC/basic",
     "https://m.stock.naver.com/worldstock/index/.IXIC/total"),
    ("환율", "원·달러", "EXCHANGE",
     "https://m.stock.naver.com/marketindex/exchange/FX_USDKRW"),
    ("반도체", "삼성전자", "https://m.stock.naver.com/api/stock/005930/basic",
     "https://m.stock.naver.com/domestic/stock/005930/total"),
    ("반도체", "SK하이닉스", "https://m.stock.naver.com/api/stock/000660/basic",
     "https://m.stock.naver.com/domestic/stock/000660/total"),
]

EXCHANGE_URL = ("https://m.stock.naver.com/front-api/marketIndex/productDetail"
                "?category=exchange&reutersCode=FX_USDKRW")


def fetch_market():
    out = []
    for group, name, api, link in MARKET_ITEMS:
        try:
            if api == "EXCHANGE":
                d = json.loads(fetch(EXCHANGE_URL, headers={"User-Agent": MOBILE_UA}))["result"]
            else:
                d = json.loads(fetch(api, headers={"User-Agent": MOBILE_UA}))
            price = d.get("closePrice")
            diff = d.get("compareToPreviousClosePrice") or d.get("fluctuations")
            ratio = d.get("fluctuationsRatio")
            if price is None or ratio is None:
                continue
            out.append({
                "group": group, "name": name, "price": str(price),
                "diff": str(diff), "ratio": float(str(ratio).replace(",", "")), "url": link,
            })
        except Exception as e:
            log(f"  ! 시세 실패 {name} ({type(e).__name__}) - 건너뜀")
        time.sleep(0.25)
    return out


def safe(fn, *args, **kwargs):
    """소스 하나가 죽어도 전체 수집은 계속되게 한다."""
    try:
        result = fn(*args, **kwargs)
        time.sleep(REQUEST_GAP)
        return result
    except urllib.error.HTTPError as e:
        log(f"  ! HTTP {e.code} - 건너뜀")
    except Exception as e:
        log(f"  ! 실패({type(e).__name__}: {e}) - 건너뜀")
    time.sleep(REQUEST_GAP)
    return []


# ------------------------------------------------------------------- 메인

def collect(config):
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    secret = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    use_naver = bool(cid and secret)
    log("네이버 API: " + ("사용" if use_naver else "미설정 → 구글뉴스만 사용"))

    pub_cfg = config.get("publisher_rss", {})
    pub_feeds = pub_cfg.get("feeds", []) if pub_cfg.get("enabled") else []

    dd = config.get("dedupe", {})
    threshold = dd.get("similarity_threshold", 0.45)
    shingle_size = dd.get("shingle_size", 2)
    max_per_cat = config.get("site", {}).get("max_per_category", 60)

    result = {}
    stats = {}
    for cat in config["categories"]:
        log(f"[{cat['name']}] 수집 시작")
        src = cat.get("sources", {})
        raw = []
        for topic in src.get("google_topic", []):
            raw += safe(google_topic, topic)
        for q in src.get("google_search", []):
            raw += safe(google_search, q)
        if use_naver:
            for q in src.get("naver_search", []):
                raw += safe(naver_search, q, cid, secret)
        for feed in pub_feeds:
            if feed.get("enabled", True) and feed.get("category") == cat["id"]:
                got = safe(publisher_feed, feed)
                log(f"  언론사 RSS: {feed['name']} → {len(got)}건")
                raw += got

        # URL 기준 1차 제거
        seen_urls = set()
        deduped = []
        blocked = 0
        for a in raw:
            if is_blocked(a["url"], a["source"]):
                blocked += 1
                continue
            u = a["url"].split("?")[0]
            if u in seen_urls:
                continue
            seen_urls.add(u)
            deduped.append(a)

        deduped.sort(key=lambda a: a["published"], reverse=True)
        clustered = cluster_articles(deduped, threshold, shingle_size)
        clustered.sort(key=lambda a: (a["related_total"], a["published"]), reverse=True)
        clustered = clustered[:max_per_cat]

        result[cat["id"]] = clustered
        stats[cat["id"]] = {"raw": len(raw), "unique": len(deduped), "shown": len(clustered)}
        log(f"[{cat['name']}] 원본 {len(raw)} → 차단 {blocked} → 중복제거 {len(deduped)} → 묶음 {len(clustered)}")

    log("시세 수집")
    market = fetch_market()
    log(f"시세 {len(market)}/{len(MARKET_ITEMS)}건")

    return result, stats, use_naver, market


def write_output(config, articles, stats, use_naver, market):
    now = datetime.now(KST)
    slot = "morning" if now.hour < 13 else "evening"
    payload = {
        "generated_at": now.isoformat(),
        "generated_label": f"{now:%Y년 %m월 %d일 %H:%M}",
        "slot": slot,
        "naver_enabled": use_naver,
        "categories": [
            {"id": c["id"], "name": c["name"], "emoji": c.get("emoji", ""), "desc": c.get("desc", "")}
            for c in config["categories"]
        ],
        "stats": stats,
        "market": market,
        "articles": articles,
    }

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    latest = os.path.join(DATA_DIR, "latest.json")
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    # 아카이브는 "그날 뭐가 이슈였나" 훑는 용도라 가볍게 저장한다.
    # 관련기사 링크를 빼면 절반 이하로 줄어든다 (URL이 용량의 45%를 차지한다).
    slim = dict(payload)
    slim["articles"] = {
        cid: [{k: v for k, v in a.items() if k != "related"} for a in arts[:ARCHIVE_PER_CATEGORY]]
        for cid, arts in articles.items()
    }
    slim["slim"] = True
    stamp = f"{now:%Y-%m-%d}-{slot}"
    with open(os.path.join(ARCHIVE_DIR, f"{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))

    prune_archive(config)
    rebuild_index()
    log(f"저장 완료 → docs/data/latest.json + archive/{stamp}.json")


def prune_archive(config):
    keep = config.get("site", {}).get("archive_keep_days", 60)
    cutoff = (datetime.now(KST) - timedelta(days=keep)).strftime("%Y-%m-%d")
    for name in os.listdir(ARCHIVE_DIR):
        if name.endswith(".json") and name[:10] < cutoff:
            os.remove(os.path.join(ARCHIVE_DIR, name))
            log(f"아카이브 정리: {name}")


def rebuild_index():
    """아카이브 목록을 index.json으로 만든다. 프론트의 날짜 선택기가 읽는다."""
    entries = []
    for name in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
        if not name.endswith(".json"):
            continue
        stamp = name[:-5]
        date, _, slot = stamp.rpartition("-")
        entries.append({
            "file": name,
            "date": date,
            "slot": slot,
            "label": f"{date} {'오전' if slot == 'morning' else '오후'}",
        })
    with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"snapshots": entries}, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 결과 요약만 출력")
    args = ap.parse_args()

    load_dotenv()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    articles, stats, use_naver, market = collect(config)
    total = sum(len(v) for v in articles.values())
    if total == 0:
        log("!! 수집된 기사가 0건이다. 기존 데이터를 덮어쓰지 않고 종료한다.")
        return 1

    if args.dry_run:
        for cat in config["categories"]:
            print(f"\n=== {cat['name']} ===")
            for a in articles[cat["id"]][:5]:
                extra = f" (+{len(a['related'])}건)" if a["related"] else ""
                print(f"  · {a['title'][:60]}  [{a['source']}]{extra}")
        return 0

    write_output(config, articles, stats, use_naver, market)
    return 0


if __name__ == "__main__":
    sys.exit(main())
