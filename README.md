# 04. News Dashboard — DJ News Desk

관심 분야 뉴스를 하루 2회 자동으로 긁어와 모바일/웹에서 보는 개인 뉴스 대시보드.
**서버 없음, 비용 0원.** GitHub Actions가 수집하고 GitHub Pages가 띄운다.

## 다루는 5개 카테고리

| 탭 | 범위 |
|---|---|
| 🤝 M&A | 경영권 매각 · 사모펀드 · 스타트업 투자/엑시트 · 크로스보더 · 구조조정 |
| 🍜 K-Food | F&B 프랜차이즈 · 외식 트렌드 · K푸드 해외진출 |
| 🏛️ 정치 | 한국 정치 · 국회 · 대통령실 |
| 📈 경제 | 한국 경제 · 증시 · 금리 · 환율 · 부동산 |
| 🌍 세계 | 국제 정세 · 글로벌 경제 |

## 기능

- **홈 대시보드** — 5개 카테고리 상위 5건씩 한 화면에
- **카테고리 탭** — 카테고리별 전체 목록
- **중복 기사 묶기** — 같은 사건을 여러 매체가 쓰면 하나로 접고 `관련 N건`
- **키워드 검색** — 수집된 기사 전체에서 즉시 필터
- **나중에 보기(★)** — 브라우저에 저장, 서버 불필요
- **지난 날짜 보기** — 수집 스냅샷을 60일치 보관

## 구조

```
collect.py              수집기 (파이썬 표준 라이브러리만 사용, pip 설치 불필요)
config.json             카테고리·검색 키워드·중복제거 설정
docs/                   GitHub Pages 루트
  index.html / style.css / app.js
  data/latest.json      최신 스냅샷 (프론트가 읽는 파일)
  data/index.json       아카이브 목록
  data/archive/*.json   날짜별 스냅샷
.github/workflows/collect.yml   하루 2회 (07:00 / 19:00 KST) 자동 실행
```

## 데이터 소스

| 소스 | 키 필요 | 상태 |
|---|---|---|
| Google News RSS | 불필요 | 사용 중 |
| 언론사 RSS (연합·매경·머니투데이·조선비즈·식품음료신문) | 불필요 | 사용 중 |
| 네이버 금융 (시세) | 불필요 | 사용 중 |
| Naver 검색 API | Client ID/Secret | **네이버클라우드 API HUB로 유료 이관됨. 미사용** |

구글뉴스 RSS는 기사 요약 스니펫을 주지 않는다. **언론사 RSS가 진짜 요약을 주므로** 이쪽으로 채운다.
언론사 피드는 카테고리 전체를 주기 때문에, M&A·K-Food는 `config.json`의 `keywords`로 걸러 쓴다.

### 네이버 키 등록

1. https://developers.naver.com → 애플리케이션 등록 → 사용 API `검색`
2. 발급된 Client ID / Secret 복사
3. GitHub 저장소 → Settings → Secrets and variables → Actions → New repository secret
   - `NAVER_CLIENT_ID`
   - `NAVER_CLIENT_SECRET`

로컬에서는 `.env` 파일에 넣는다. `collect.py`가 자동으로 읽는다.

```bash
cp .env.example .env      # 최초 1회
# .env 를 열어 두 줄의 값을 채운다
python3 collect.py
```

`.env`는 `.gitignore`에 있어 커밋되지 않는다.
GitHub Actions는 `.env` 대신 저장소 Secrets를 쓰며, 환경변수가 이미 있으면 `.env`가 덮어쓰지 않는다.

## 로컬에서 돌려보기

```bash
python3 collect.py            # 수집 → docs/data/ 갱신
python3 collect.py --dry-run  # 파일 안 쓰고 결과만 확인

python3 -m http.server 8080 --directory docs
# → http://localhost:8080
```

## 키워드 바꾸기

`config.json`의 `categories[].sources.google_search` / `naver_search` 배열만 고치면 된다.
카테고리를 추가하려면 같은 형식으로 항목 하나를 더 넣으면 프론트도 자동으로 탭을 만든다.

## 알아둘 것

- 수집 결과가 0건이면 기존 데이터를 덮어쓰지 않고 종료한다.
- GitHub은 저장소에 60일간 활동이 없으면 스케줄 워크플로를 자동 중지한다. 봇 커밋이 매일 쌓이므로 실사용 중에는 문제되지 않는다.
- Actions 무료 한도는 공개 저장소면 무제한, 비공개면 월 2,000분. 이 수집은 1회 30초 남짓이라 월 30분 수준이다.
