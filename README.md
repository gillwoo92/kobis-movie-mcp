# 🎬 KOBIS 영화 MCP 서버

[영화진흥위원회 통합전산망(KOBIS) 오픈API](https://www.kobis.or.kr/kobisopenapi/homepg/main/main.do)의 무료 데이터를
[MCP](https://modelcontextprotocol.io) 도구로 제공하는 [FastMCP](https://gofastmcp.com) 단일 파일 서버입니다.
Claude Code, Claude Desktop 등 MCP 클라이언트에 연결하면 "이번 주 박스오피스 추이 알려줘" 같은 질문에 실제 데이터로 답할 수 있습니다.

## 제공 도구

| 도구 | 설명 |
|---|---|
| `weekly_new_releases` | **주차별 개봉 영화.** 그 주(월~일)에 개봉한 모든 영화와 장르·국가·감독, 박스오피스 상위 10위에 든 작품은 관람객·매출 성적까지 함께 반환 |
| `weekly_audience_ranking` | **주차별 누적 관람객 기준 순위.** 그 주 박스오피스 상위 10편을 개봉 후 누적 관람객 수로 정렬 |
| `weekly_sales_ranking` | **주차별 누적 매출액 기준 순위.** 그 주 박스오피스 상위 10편을 개봉 후 누적 매출액으로 정렬 |
| `weekly_trend` | **관람객 + 매출액 총합 대비 상승·하락 지표.** 상위 10편 총합의 전주 대비 증감, 영화별 점유율과 상승 ▲ / 하락 ▼ / 보합 / 신규 진입 ★ 판정 |

### 공통 매개변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `target_date` | 생략 | 조회할 주에 포함된 아무 날짜 (`20261108` 또는 `2026-11-08`). 그 날짜가 속한 **월~일** 주를 조회 |
| `week_type` | `0` | 순위·추이 도구 전용. `0` 주간(월~일), `1` 주말(금~일), `2` 주중(월~목) |
| `include_adult` | `false` | `weekly_new_releases` 전용. 성인물 장르 포함 여부 |

### 날짜를 생략하면

- 호출하는 순간을 기준으로 **가장 최근에 끝난 주**를 자동으로 조회합니다.
- 월요일 이른 시간처럼 최근 주가 아직 KOBIS 집계 전이면 **한 주 전 데이터로 자동 대체**하고, 결과에 `fallback_note` 를 붙여 알려줍니다.
- 날짜를 직접 지정했을 때는 대체하지 않고 "데이터 없음" 오류를 그대로 반환합니다.

## 설치

Python 3.10 이상이 필요합니다.

```bash
git clone https://github.com/gillwoo92/kobis-movie-mcp.git
cd kobis-movie-mcp
python -m venv .venv
```

```bash
# Windows
.venv\Scripts\pip install -r requirements.txt

# macOS / Linux
.venv/bin/pip install -r requirements.txt
```

외부 HTTP 호출은 표준 라이브러리(`urllib`)로 처리하므로 `fastmcp` 외에 필요한 패키지는 없습니다.

## API 키 설정

1. [KOBIS 오픈API](https://www.kobis.or.kr/kobisopenapi/homepg/main/main.do)에 회원가입 후 **키 발급**을 받습니다 (무료, 일 3,000회 호출).
2. `.env.example` 을 `.env` 로 복사하고 키를 입력합니다.

```
KOBIS_API_KEY=발급받은키
```

서버는 자기 파일 옆의 `.env` 를 읽고, 같은 이름의 환경변수가 이미 있으면 그 값을 우선합니다.
`.env` 는 `.gitignore` 에 포함되어 있으니 **절대 커밋하지 마세요.**

## 실행

```bash
# stdio (MCP 클라이언트가 직접 실행하는 방식)
.venv/bin/python kobis_mcp_server.py

# Streamable HTTP  →  http://127.0.0.1:8001/mcp
.venv/bin/python kobis_mcp_server.py --http 8001
```

Windows 에서는 `.venv/bin/python` 대신 `.venv\Scripts\python.exe` 를 사용하세요.

## MCP 클라이언트에 연결

### Claude Code

```bash
claude mcp add kobis-movie --env PYTHONIOENCODING=utf-8 -- /절대/경로/kobis-movie-mcp/.venv/bin/python /절대/경로/kobis-movie-mcp/kobis_mcp_server.py
```

또는 프로젝트 루트의 `.mcp.json` 에 직접 추가합니다. Claude Code 가 그 폴더에서 열리면 서버 사용 승인을 묻습니다.

```json
{
  "mcpServers": {
    "kobis-movie": {
      "type": "stdio",
      "command": "/절대/경로/kobis-movie-mcp/.venv/bin/python",
      "args": ["/절대/경로/kobis-movie-mcp/kobis_mcp_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

### Claude Desktop

`claude_desktop_config.json` 에 추가합니다.

```json
{
  "mcpServers": {
    "kobis-movie": {
      "command": "C:\\경로\\kobis-movie-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\경로\\kobis-movie-mcp\\kobis_mcp_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

Windows 에서는 한글 깨짐을 막기 위해 `PYTHONIOENCODING=utf-8` 을 넣는 것을 권장합니다.

## 사용 예시

연결한 뒤 Claude 에게 이렇게 물어보세요.

- "지난주에 개봉한 영화 뭐 있어?" → `weekly_new_releases`
- "11월 첫째 주 박스오피스를 누적 관객 순으로 보여줘" → `weekly_audience_ranking`
- "이번 주말 박스오피스 매출 누적 순위" → `weekly_sales_ranking` (`week_type=1`)
- "지난주 극장가 전주 대비 어땠어? 뜨는 영화랑 지는 영화 알려줘" → `weekly_trend`

`weekly_trend` 응답 예시 (요약):

```json
{
  "range": "2026-11-02 ~ 2026-11-08",
  "totals": {
    "audience": { "this_week": 802767, "prev_week": 928461, "change_pct": -13.5, "direction": "하락 ▼" },
    "sales":    { "this_week": 8064098110, "prev_week": 9240300510, "change_pct": -12.7, "direction": "하락 ▼" },
    "top10_share_of_market_sales_pct": 80.0,
    "combined_change_pct": -13.1,
    "combined_direction": "하락 ▼"
  },
  "movies": [
    { "rank": 1, "title": "…", "audience_share_pct": 25.5, "sales_share_pct": 25.7,
      "audience_change_pct": -39.0, "sales_change_pct": -39.5, "trend": "하락 ▼" }
  ],
  "summary": { "rising": ["…"], "falling": ["…"], "new_entries": ["…"] }
}
```

## 지표 계산 방식

- **박스오피스 범위**: KOBIS 주간 박스오피스 API 가 **상위 10편**만 제공하므로 순위·추이 도구는 이 10편 기준입니다.
- **누적 순위**: 그 주 상위 10편을 `audiAcc`(누적 관객) / `salesAcc`(누적 매출)로 다시 정렬하고, 10편 누적 합계 대비 비중(`share_of_top10_acc_pct`)을 함께 줍니다.
- **총합 대비 점유율**: 영화별 이번 주 관람객·매출이 상위 10편 합계에서 차지하는 비율. `combined_share_pct` 는 둘의 평균입니다.
- **전주 대비 증감**
  - 총합: 이번 주 상위 10편 합계 vs 전주 상위 10편 합계
  - 영화별: KOBIS 가 제공하는 전주 대비 증감률(`audiChange`, `salesChange`)
  - `combined_change_pct` = 관람객·매출 증감률 평균 → `+1%` 초과 상승 ▲, `-1%` 미만 하락 ▼, 그 사이 보합
  - 신규 진입작은 비교 대상이 없어 `신규 진입 ★` 으로 표시
- **시장 점유율**: `top10_share_of_market_sales_pct` 는 KOBIS `salesShare`(전체 극장 매출 대비 점유율)의 합입니다.

## 알아두면 좋은 점

- **개봉작 조회 첫 호출은 15~20초** 걸릴 수 있습니다. KOBIS 영화목록 API 가 개봉일을 연도 단위로만 필터링해서, 그해 등록작(약 2천 편)을 병렬로 모두 받은 뒤 해당 주만 골라내기 때문입니다. 받은 목록은 **6시간 메모리 캐시**되어 이후 호출은 즉시 끝납니다.
- **시사회 효과**: 전주에 유료 시사회 관객만 있던 영화는 정식 개봉 주에 증감률이 수천 %로 튈 수 있습니다.
- **호출 한도**: KOBIS 무료 키는 하루 3,000회입니다. 개봉작 첫 조회에 약 20회, 나머지 도구는 1~2회를 사용합니다.

## 데이터 출처

모든 영화 데이터는 [영화진흥위원회 영화관입장권 통합전산망](https://www.kobis.or.kr) 오픈API에서 제공합니다.
