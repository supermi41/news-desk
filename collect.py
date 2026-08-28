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
STORE_DIR = os.path.join(DATA_DIR, "cat")      # 카테고리별 누적 저장소

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
REQUEST_GAP = 0.4        # 초. 소스에 예의를 지킨다.
RELATED_LINK_LIMIT = 12  # 관련기사 링크 저장 상한 (용량 관리)
HOME_PER_CATEGORY = 10   # latest.json(홈)에 담을 카테고리당 기사 수
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
    # 워드프레스 RSS가 요약 끝에 붙이는 "The post ... appeared first on ..." 제거
    s = re.sub(r"The post .*? appeared first on.*$", "", s, flags=re.S)
    s = s.replace("​", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


# 매일경제 등 일부 피드는 "+09:00" 처럼 콜론을 넣어 표기한다.
# RFC 822는 "+0900" 형식이라 콜론이 있으면 파이썬이 타임존을 통째로 무시하고
# 순진한(naive) 시각을 돌려준다. 그걸 UTC로 오해하면 9시간이 밀린다.
TZ_COLON = re.compile(r"([+-]\d{2}):(\d{2})\s*$")


def parse_date(raw, default_tz=None):
    """RFC822 날짜 문자열 → KST datetime. 실패하면 현재 시각."""
    if raw:
        try:
            dt = parsedate_to_datetime(TZ_COLON.sub(r"\1\2", str(raw).strip()))
            if dt.tzinfo is None:
                # 타임존이 없으면 국내 피드로 보고 KST로 읽는다 (해외 피드는 GMT를 명시한다)
                dt = dt.replace(tzinfo=default_tz or KST)
            dt = dt.astimezone(KST)
            now = datetime.now(KST)
            # 미래 시각은 신뢰하지 않는다. 정렬이 통째로 뒤틀린다.
            return now if dt > now + timedelta(minutes=10) else dt
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


# 보도자료 정정문, 광고성 공지 등은 기사로 보지 않는다
NOISE_TITLE = re.compile(r"(C\s?O\s?R\s?R\s?E\s?C\s?T\s?I\s?O\s?N|^\s*/.*/\s*$|"
                         r"Media Advisory|Photo Release|정정보도|광고문의)", re.IGNORECASE)


def is_noise(title):
    return bool(NOISE_TITLE.search(title or ""))


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

def parse_rss(xml_bytes, default_source="", default_tz=None):
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
            "summary": desc[:400],
            "published": parse_date(item.findtext("pubDate"), default_tz).isoformat(),
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
            "summary": clean_text(it.get("description", ""))[:400],
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
    items = parse_rss(fetch(feed["url"], cache=True), feed["name"],
                      default_tz=timezone.utc if feed.get("lang") == "en" else None)
    # 어느 매체 피드인지 알고 있으므로 이름을 확정한다.
    # 일부 피드는 <source>에 사진 크레딧 같은 엉뚱한 값을 넣는다.
    for it in items:
        it["source"] = feed["name"]
        if feed.get("lang") == "en":
            it["lang"] = "en"          # 화면에서 외신 배지를 달기 위한 표시
    kws = feed.get("keywords")
    if kws:
        items = [
            it for it in items
            if any(k in it["title"] or k in it["summary"] for k in kws)
        ]
    return items


# --------------------------------------------------------- 최근 딜 (뉴스 기반)
# thevc.kr은 403으로 막혀 있고, 기사 본문에서 정규식으로 표를 채우면 오추출이 많다.
# 그래서 표가 아니라 "딜 기사 목록"으로 두고, 제목·요약에서 확실히 읽히는
# 금액·라운드만 배지로 붙인다. 못 읽으면 비워둔다 — 절대 추정하지 않는다.

DEAL_FEEDS = [
    ("플래텀", "https://platum.kr/feed"),
    ("벤처스퀘어", "https://www.venturesquare.net/feed"),
    ("스타트업레시피", "https://startuprecipe.co.kr/feed"),
]
DEAL_QUERIES = [
    "스타트업 시리즈 투자 유치 억원",
    "시드 프리A 투자 유치 스타트업",
    "경영권 매각 우선협상대상자 선정",
    "사모펀드 인수 지분 인수",
]

# 딜 기사인지 판단 (제목 기준)
DEAL_HINT = re.compile(r"(투자\s*유치|유치|투자를?\s*받|시리즈\s*[A-Ea-e]|시드|프리\s*IPO|라운드|"
                       r"인수|합병|매각|지분\s*취득|우선협상|본입찰)")
# 배지로 쓸 금액: "1,000억 원", "150억원", "2조" 등
DEAL_AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(조|억)\s*원?")
DEAL_ROUND = re.compile(r"(시리즈\s*[A-Ea-e][\-\+]?\d?|프리\s*[A-Ea-e]|Pre-?[A-Ea-e]|시드|브릿지|프리\s*IPO|Pre-?IPO)",
                        re.IGNORECASE)


# Target(피투자·피인수 회사)은 한국 기사 제목의 "회사명, ~" 형식에서만 뽑는다.
# 서술구가 딸려오면 틀린 값이 들어가므로 공백 1개까지만 허용하고 나머지는 비워둔다.
DEAL_TARGET = re.compile(r"^\s*(?:\[[^\]]{1,12}\]\s*)?([가-힣A-Za-z0-9][가-힣A-Za-z0-9·\.\-]{1,13}(?:\s[가-힣A-Za-z0-9·\.\-]{1,10})?)\s*[,·]")
# 회사가 아닌 것들 (협회·부처·일반명사)은 Target으로 쓰지 않는다
NOT_A_TARGET = {"VC협회", "협회", "중기부", "중소벤처기업부", "금융위", "금감원", "산업부",
                "정부", "국회", "서울시", "경기도", "한국거래소", "코스닥", "코스피",
                "스타트업", "벤처", "업계", "시장", "특징주", "단독", "속보", "종합"}
# 서술어가 섞였으면 회사명이 아니다
NOT_A_NAME = re.compile(r"(했|한다|하는|되는|된다|으로|에서|에게|보다|까지|부터|이번|올해|내년|지난)")
# 투자자로 볼 수 있는 이름 형태
DEAL_INVESTOR = re.compile(r"[가-힣A-Za-z0-9·\.]{2,22}(?:인베스트먼트|인베스트|벤처스|벤처투자|캐피탈|캐피털|"
                           r"파트너스|자산운용|액셀러레이터|어드바이저스?|프라이빗에쿼티|PE|은행|증권)")
DEAL_EV = re.compile(r"기업\s*가치\D{0,8}(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(조|억)")
DEAL_STAKE = re.compile(r"지분(?:율)?\s*(?:약\s*)?(\d{1,3}(?:\.\d+)?)\s*%")


def _amount_str(m):
    return f"{m.group(1)}{m.group(2)}원"


def deal_row(article):
    """
    기사 하나에서 표 한 행을 만든다.
    확실히 읽히는 것만 채우고 나머지는 None으로 둔다 (화면에서 '정보 없음'으로 표시).
    추정하거나 계산해서 채우지 않는다.
    """
    title = article["title"]
    summary = article.get("summary", "")
    both = title + " " + summary

    target = None
    m = DEAL_TARGET.match(title)
    if m:
        cand = m.group(1).strip()
        if (not NOT_A_NAME.search(cand)
                and not DEAL_INVESTOR.fullmatch(cand)
                and cand not in NOT_A_TARGET):
            target = cand

    # 금액은 제목에서만. 요약까지 보면 무관한 숫자가 딸려온다.
    amount = None
    am = re.search(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(조|억)\s*원?\s*"
                   r"(?:규모|투자|유치|조달|인수|매각)", title)
    if not am:
        am = re.search(r"(?:투자|유치|조달|인수|매각|규모)\D{0,4}"
                       r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(조|억)\s*원?", title)
    if am:
        amount = _amount_str(am)

    rnd = DEAL_ROUND.search(title)
    ev = DEAL_EV.search(both)
    stake = DEAL_STAKE.search(both)
    investors = [i for i in dict.fromkeys(DEAL_INVESTOR.findall(both))][:3]

    return {
        "date": article["published"][:10],
        "investors": investors,
        "target": target,
        "sector": None,
        "round": rnd.group(0).strip() if rnd else None,
        "ev": f"{ev.group(1)}{ev.group(2)}원" if ev else None,
        "amount": amount,
        "stake": f"{stake.group(1)}%" if stake else None,
        "title": title,
        "url": article["url"],
        "source": article["source"],
        "from_dart": False,
    }


def collect_deals(threshold, shingle_size):
    raw = []
    for name, url in DEAL_FEEDS:
        raw += safe(lambda u=url, n=name: parse_rss(fetch(u, cache=True), n))
    for q in DEAL_QUERIES:
        raw += safe(google_search, q)

    seen, items = set(), []
    for a in raw:
        if is_blocked(a["url"], a["source"]) or not DEAL_HINT.search(a["title"]):
            continue
        key = a["url"].split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        items.append(a)

    items.sort(key=lambda a: a["published"], reverse=True)
    clustered = cluster_articles(items, threshold, shingle_size)

    rows = [deal_row(a) for a in clustered]

    # 같은 회사 딜이 여러 기사로 흩어지면 한 행으로 합친다.
    # 기사마다 읽히는 칸이 달라서, 합치면 표가 더 촘촘해진다.
    by_target = {}
    merged = []
    for r in rows:
        key = r["target"]
        if not key:
            merged.append(r)
            continue
        if key not in by_target:
            by_target[key] = r
            merged.append(r)
            continue
        base = by_target[key]
        for field in ("round", "amount", "ev", "stake"):
            if not base[field] and r[field]:
                base[field] = r[field]
        if not base["investors"] and r["investors"]:
            base["investors"] = r["investors"]

    filled = lambda r: sum(1 for k in ("target", "round", "amount", "ev", "stake") if r[k]) + bool(r["investors"])
    merged.sort(key=lambda r: (filled(r), r["date"]), reverse=True)
    return merged[:25]


# --------------------------------------------------------- 외신 제목 번역
# MyMemory 무료 API. 키가 없어도 하루 5,000단어까지 쓸 수 있다.
# 제목만 번역한다(120건 x 약 12단어 = 하루 3,000단어 수준). 요약까지 하면 한도를 넘는다.
# TRANSLATE_EMAIL 을 넣으면 하루 50,000단어로 늘어난다.
#
# 기계번역이라 틀릴 수 있다. 그래서 원문을 버리지 않고 함께 저장해 화면에 같이 보여준다.

MYMEMORY = "https://api.mymemory.translated.net/get"
TRANSLATE_MAX = 220          # 한 번 수집에서 새로 번역할 최대 건수


def translate_ko(text, email=""):
    if not text or not text.strip():
        return None
    params = {"q": text[:480], "langpair": "en|ko"}
    if email:
        params["de"] = email
    try:
        d = json.loads(fetch(f"{MYMEMORY}?{urllib.parse.urlencode(params)}", timeout=15))
    except Exception:
        return None
    if str(d.get("responseStatus")) != "200":
        return None
    out = (d.get("responseData", {}) or {}).get("translatedText", "").strip()
    # 번역이 실패하면 원문을 그대로 돌려주는 경우가 있다. 그건 번역이 아니다.
    if not out or out.upper() == text.upper():
        return None
    return out


def load_existing_titles():
    """이미 번역해 둔 제목을 다시 번역하지 않도록 누적 저장소에서 불러온다."""
    cache = {}
    if not os.path.isdir(STORE_DIR):
        return cache
    for name in os.listdir(STORE_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(STORE_DIR, name), encoding="utf-8") as f:
                for a in json.load(f).get("articles", []):
                    if a.get("title_ko"):
                        cache[a["url"]] = a["title_ko"]
        except (json.JSONDecodeError, OSError):
            continue
    return cache


def translate_foreign(articles_by_cat):
    """외신 기사 제목에 한국어 번역을 붙인다. 실패하면 원문 그대로 둔다."""
    email = os.environ.get("TRANSLATE_EMAIL", "").strip()
    cache = load_existing_titles()
    todo = []
    for arts in articles_by_cat.values():
        for a in arts:
            if a.get("lang") != "en":
                continue
            hit = cache.get(a["url"])
            if hit:
                a["title_ko"] = hit
            else:
                todo.append(a)

    if not todo:
        log("  번역할 새 외신 없음")
        return
    todo = todo[:TRANSLATE_MAX]
    done = 0
    for a in todo:
        ko = translate_ko(a["title"], email)
        if ko:
            a["title_ko"] = ko
            done += 1
        time.sleep(0.25)
    log(f"  외신 제목 번역 {done}/{len(todo)}건" + (" (이메일 등록됨)" if email else " (익명 한도)"))


# --------------------------------------------------------- 딜 (DART 공시)
# 상장사 M&A는 공시 의무가 있어 금액·지분율이 정확하다. 기사에서 긁는 것과 비교가 안 된다.
# DART_API_KEY가 없으면 이 단계는 통째로 건너뛴다.

DART_BASE = "https://opendart.fss.or.kr/api"
DART_DAYS = 88          # 최근 며칠치 공시를 볼지 (DART list API는 90일까지 허용)
DART_MAX_CORPS = 200    # 상세 조회 호출 상한 (하루 한도는 20,000건이라 여유가 크다)

# 공시 종류 → 상세 API. 금액이 나오는 것만 쓴다.
DART_REPORTS = [
    ("타법인주식및출자증권양수결정", "otcprStkInvscrInhDecsn", "지분 취득"),
    ("타법인주식및출자증권양도결정", "otcprStkInvscrTrfDecsn", "지분 매각"),
    ("영업양수결정", "bsnInhDecsn", "영업 양수"),
    ("영업양도결정", "bsnTrfDecsn", "영업 양도"),
    ("주식교환·이전결정", "stkExtrDecsn", "주식 교환·이전"),
    ("회사합병결정", "cmpMgDecsn", "합병"),
]


def dart_get(path, **params):
    q = urllib.parse.urlencode(params)
    return json.loads(fetch(f"{DART_BASE}/{path}.json?{q}", timeout=25))


def krw(raw):
    """1234567890 → '12억원'. 공시 금액은 원 단위라 그대로 두면 못 읽는다."""
    try:
        n = int(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n >= 10 ** 12:
        return f"{n / 10 ** 12:,.2f}조원".replace(".00조", "조")
    if n >= 10 ** 8:
        return f"{n / 10 ** 8:,.0f}억원"
    return f"{n:,}원"


def pct(raw):
    try:
        v = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return f"{v:g}%" if v > 0 else None


def dart_deals(key):
    """최근 공시에서 딜 표 행을 만든다. 값은 전부 공시 원문 그대로다."""
    end = datetime.now(KST)
    bgn = end - timedelta(days=DART_DAYS)
    wanted = {name: (ep, kind) for name, ep, kind in DART_REPORTS}

    # 1) 공시 목록에서 대상 회사(corp_code)를 추린다
    targets = {}
    for page in range(1, 12):
        d = dart_get("list", crtfc_key=key, bgn_de=f"{bgn:%Y%m%d}", end_de=f"{end:%Y%m%d}",
                     pblntf_ty="B", page_count=100, page_no=page)
        if d.get("status") != "000":
            break
        for it in d.get("list", []):
            for name in wanted:
                if name in it["report_nm"]:
                    targets.setdefault(it["corp_code"], (wanted[name][0], wanted[name][1]))
                    break
        if page >= int(d.get("total_page", 1)):
            break
        time.sleep(0.15)

    log(f"  DART 대상 회사 {len(targets)}곳")

    # 2) 회사별 상세 공시를 읽는다
    rows = []
    for corp_code, (endpoint, kind) in list(targets.items())[:DART_MAX_CORPS]:
        try:
            d = dart_get(endpoint, crtfc_key=key, corp_code=corp_code,
                         bgn_de=f"{bgn:%Y%m%d}", end_de=f"{end:%Y%m%d}")
        except Exception:
            time.sleep(0.2)
            continue
        if d.get("status") != "000":
            time.sleep(0.2)
            continue
        for r in d.get("list", []):
            rows.append(dart_row(r, kind))
        time.sleep(0.2)

    # 최신순, 금액이 있는 건 우선
    rows.sort(key=lambda r: (bool(r["amount"]), r["date"]), reverse=True)
    return rows


def dart_date(raw):
    """'2026년 08월 26일' → '2026-08-26'"""
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(raw or ""))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


# 외부평가 의견문에 적힌 가치 산출 결과를 그대로 옮긴다. 직접 계산하지 않는다.
EV_RANGE = re.compile(r"([\d,]+)\s*(백만원|억원|백만|억)\s*(?:에서|~|∼|-)\s*([\d,]+)\s*(백만원|억원|백만|억)")
EV_SINGLE = re.compile(r"(?:가치|평가액|평가가액)[^\d]{0,12}([\d,]+)\s*(백만원|억원)")


def _ev_unit(num, unit):
    """공시는 백만원 단위를 즐겨 쓴다. 억원으로 맞춰준다."""
    try:
        n = float(str(num).replace(",", ""))
    except ValueError:
        return None
    if unit.startswith("백만"):
        n = n / 100.0           # 100백만원 = 1억원
    return f"{n:,.0f}억원" if n >= 1 else f"{n:,.2f}억원"


def dart_ev(text):
    if not text:
        return None
    t = re.sub(r"\s+", " ", str(text))
    m = EV_RANGE.search(t)
    if m:
        lo = _ev_unit(m.group(1), m.group(2))
        hi = _ev_unit(m.group(3), m.group(4))
        if lo and hi:
            return f"{lo}~{hi}"
    m = EV_SINGLE.search(t)
    if m:
        return _ev_unit(m.group(1), m.group(2))
    return None


def dart_row(r, kind):
    """공시 필드를 표 한 행으로 옮긴다. 없는 값은 None으로 두고 절대 계산하지 않는다."""
    # 지분 양수/양도는 발행회사가 Target, 영업 양수/양도는 거래상대방이 Target
    tidy = lambda v: re.sub(r"\s+", " ", str(v)).strip() if v else None
    target = tidy(r.get("iscmp_cmpnm") or r.get("dlptn_cmpnm") or r.get("mgptncmp_cmpnm")
                  or r.get("extrptncmp_cmpnm"))
    sector = tidy(r.get("iscmp_mbsn") or r.get("inh_bsn") or r.get("dlptn_mbsn")
                  or r.get("mgptncmp_mbsn"))
    amount = krw(r.get("inhdtl_inhprc") or r.get("inh_prc") or r.get("trfdtl_trfprc"))
    stake = pct(r.get("atinh_eqrt"))
    return {
        "date": dart_date(r.get("bddd")),
        "investors": [r.get("corp_name")] if r.get("corp_name") else [],
        "target": (target or "").strip()[:40] or None,
        "sector": (sector or "").strip()[:32] or None,
        "round": kind,                       # M&A라 VC 라운드 개념이 없다. 거래 유형을 넣는다
        # 외부평가 의견에 적힌 가치 산출 결과를 그대로 옮긴다 (없으면 비워둔다)
        "ev": dart_ev(r.get("exevl_op")),
        "amount": amount,
        "stake": stake,
        "title": f"{r.get('corp_name','')} {kind} 공시",
        "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.get('rcept_no','')}",
        "source": "DART 공시",
        "from_dart": True,
    }


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
            if is_blocked(a["url"], a["source"]) or is_noise(a["title"]):
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

    log("외신 제목 번역")
    translate_foreign(result)

    log("최근 딜 수집")
    deals = []
    dart_key = os.environ.get("DART_API_KEY", "").strip()
    if dart_key:
        try:
            deals = dart_deals(dart_key)
            log(f"  DART 공시 딜 {len(deals)}건")
        except Exception as e:
            log(f"  ! DART 실패({type(e).__name__}: {e}) - 뉴스만 사용")
    else:
        log("  DART_API_KEY 없음 - 뉴스에서만 추출")
    deals += collect_deals(threshold, shingle_size)
    deals = deals[:80]
    withamt = sum(1 for d in deals if d.get("amount"))
    log(f"딜 {len(deals)}건 (금액 있는 것 {withamt}건)")

    log("시세 수집")
    market = fetch_market()
    log(f"시세 {len(market)}/{len(MARKET_ITEMS)}건")

    return result, stats, use_naver, market, deals


def build_source_list(config, stats):
    """
    지금 어디서 뉴스를 가져오고 있는지 화면에 그대로 보여주기 위한 목록.
    config를 읽어서 만들기 때문에 설정을 바꾸면 화면도 따라 바뀐다.
    """
    pub = config.get("publisher_rss", {})
    feeds = pub.get("feeds", []) if pub.get("enabled") else []
    out = []
    for cat in config["categories"]:
        src = cat.get("sources", {})
        entries = []
        for topic in src.get("google_topic", []):
            entries.append({"kind": "구글뉴스 토픽", "name": topic, "detail": "카테고리 헤드라인"})
        for q in src.get("google_search", []):
            entries.append({"kind": "구글뉴스 검색", "name": q, "detail": "최근 2일"})
        if src.get("naver_search") and os.environ.get("NAVER_CLIENT_ID"):
            for q in src["naver_search"]:
                entries.append({"kind": "네이버 검색", "name": q, "detail": ""})
        for f in feeds:
            if f.get("enabled", True) and f.get("category") == cat["id"]:
                kws = f.get("keywords")
                entries.append({
                    "kind": "외신 RSS" if f.get("lang") == "en" else "언론사 RSS",
                    "name": f["name"],
                    "detail": (f"키워드 {len(kws)}개로 필터" if kws else "카테고리 전체"),
                    "url": f["url"],
                })
        out.append({
            "id": cat["id"], "name": cat["name"], "emoji": cat.get("emoji", ""),
            "entries": entries,
            "raw": stats.get(cat["id"], {}).get("raw", 0),
            "stored": stats.get(cat["id"], {}).get("stored", 0),
        })
    return out


def merge_store(cat_id, fresh, keep_days, max_items):
    """
    새로 수집한 기사를 카테고리 누적 파일에 합친다.
    수집할 때마다 덮어쓰던 것을 바꿔서, 기사가 날짜가 지나도 계속 쌓이게 한다.
    """
    os.makedirs(STORE_DIR, exist_ok=True)
    path = os.path.join(STORE_DIR, f"{cat_id}.json")

    old = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f).get("articles", [])
        except (json.JSONDecodeError, OSError):
            old = []

    # 1) URL 기준 병합. 새로 온 쪽이 관련기사를 더 많이 물고 있으면 그쪽으로 갱신한다.
    by_url = {a["url"]: a for a in old}
    added = 0
    for a in fresh:
        prev = by_url.get(a["url"])
        if prev is None:
            by_url[a["url"]] = a
            added += 1
        elif a.get("related_total", 0) > prev.get("related_total", 0):
            by_url[a["url"]] = a

    # 2) 제목 기준으로 한 번 더 정리한다.
    #    매 수집마다 클러스터 대표가 바뀔 수 있어 같은 사건이 두 줄로 남는 걸 막는다.
    by_title = {}
    for a in by_url.values():
        key = normalize_title(a["title"])
        prev = by_title.get(key)
        if prev is None or a.get("related_total", 0) > prev.get("related_total", 0):
            by_title[key] = a

    items = list(by_title.values())
    cutoff = (datetime.now(KST) - timedelta(days=keep_days)).isoformat()
    items = [a for a in items if a.get("published", "") >= cutoff]
    items.sort(key=lambda a: a["published"], reverse=True)
    items = items[:max_items]

    with open(path, "w", encoding="utf-8") as f:
        json.dump({"category": cat_id,
                   "updated_at": datetime.now(KST).isoformat(),
                   "count": len(items),
                   "articles": items},
                  f, ensure_ascii=False, separators=(",", ":"))
    return items, added


def pick_for_home(items, limit, per_source=2):
    """
    홈에 실을 기사를 고른다.
    한 매체가 같은 시각에 수십 건을 쏟아내면 홈이 그 매체로 도배되므로
    매체당 몇 건까지만 넣고, 자리가 남으면 그때 나머지를 채운다.
    """
    picked, used = [], {}
    for a in items:
        src = a.get("source", "")
        if used.get(src, 0) >= per_source:
            continue
        used[src] = used.get(src, 0) + 1
        picked.append(a)
        if len(picked) >= limit:
            return picked
    for a in items:                       # 그래도 모자라면 남은 것으로 채운다
        if a not in picked:
            picked.append(a)
            if len(picked) >= limit:
                break
    return picked


def write_output(config, articles, stats, use_naver, market, deals):
    now = datetime.now(KST)
    site = config.get("site", {})
    keep_days = site.get("store_keep_days", 21)
    max_items = site.get("store_max_per_category", 2500)

    # 누적 저장소에 합치고, 홈에는 최신 일부만 싣는다
    totals, home = {}, {}
    for cid, fresh in articles.items():
        merged, added = merge_store(cid, fresh, keep_days, max_items)
        totals[cid] = len(merged)
        home[cid] = pick_for_home(merged, HOME_PER_CATEGORY)
        stats.setdefault(cid, {})["stored"] = len(merged)
        stats[cid]["added"] = added
        log(f"  누적 [{cid}] {len(merged)}건 (이번에 새로 {added}건)")
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
        "totals": totals,
        "sources": build_source_list(config, stats),
        "market": market,
        "deals": deals,
        "dart_enabled": bool(os.environ.get("DART_API_KEY", "").strip()),
        "articles": home,
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
        for cid, arts in home.items()
    }
    slim["deals"] = []
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

    articles, stats, use_naver, market, deals = collect(config)
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

    write_output(config, articles, stats, use_naver, market, deals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
