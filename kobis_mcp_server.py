"""
영화진흥위원회(KOBIS) 오픈API MCP 서버 (FastMCP 단일 파일)

도구
- weekly_new_releases     : 주차별 개봉 영화 (그 주 월~일 개봉작 + 박스오피스 성적)
- weekly_audience_ranking : 주차별 박스오피스 영화의 누적 관람객 기준 순위
- weekly_sales_ranking    : 주차별 박스오피스 영화의 누적 매출액 기준 순위
- weekly_trend            : 주차별 관람객·매출액 총합 대비 상승/하락 지표 (전주 대비)

API 키
    같은 폴더의 .env 파일(또는 환경변수)에서 KOBIS_API_KEY 를 읽습니다.

실행
    python -m venv .venv
    .venv\\Scripts\\pip install fastmcp
    .venv\\Scripts\\python kobis_mcp_server.py                 # stdio (Claude Code / Claude Desktop 등)
    .venv\\Scripts\\python kobis_mcp_server.py --http 8001     # Streamable HTTP (http://127.0.0.1:8001/mcp)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

BASE_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest"
WEEKLY_BOXOFFICE_URL = f"{BASE_URL}/boxoffice/searchWeeklyBoxOfficeList.json"
MOVIE_LIST_URL = f"{BASE_URL}/movie/searchMovieList.json"
TIMEOUT = 15
MOVIE_LIST_PAGE_SIZE = 100  # KOBIS 최대값
MOVIE_LIST_CACHE_TTL = 6 * 60 * 60  # 연도별 영화목록 캐시 6시간

WEEK_TYPES = {0: "주간(월~일)", 1: "주말(금~일)", 2: "주중(월~목)"}

mcp = FastMCP(
    "kobis-movie",
    instructions=(
        "영화진흥위원회(KOBIS) 오픈API로 한국 극장가의 주차별 개봉작, 누적 관람객/매출액 순위, "
        "전주 대비 상승·하락 지표를 조회합니다. 날짜는 YYYYMMDD 또는 YYYY-MM-DD 형식이며, "
        "해당 날짜가 속한 주(월~일)를 조회합니다. 생략하면 가장 최근에 끝난 주를 조회합니다. "
        "박스오피스 데이터는 KOBIS 제공 범위인 주간 상위 10편 기준입니다."
    ),
)


# ---------------------------------------------------------------------------
# 설정 / 공통 헬퍼
# ---------------------------------------------------------------------------
class ApiError(Exception):
    pass


class NoDataError(ApiError):
    """요청은 성공했지만 해당 주의 박스오피스가 아직 집계되지 않음."""


def _load_dotenv() -> None:
    """스크립트 옆 .env 를 읽어 환경변수에 없는 값만 채운다 (python-dotenv 없이)."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


def _api_key() -> str:
    key = os.environ.get("KOBIS_API_KEY", "").strip()
    if not key:
        raise ApiError("KOBIS_API_KEY 가 설정되지 않았습니다. .env 파일에 KOBIS_API_KEY=발급키 를 추가하세요.")
    return key


def _kobis_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode({"key": _api_key(), **params})
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "kobis-movie-mcp/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ApiError(f"KOBIS API 요청 실패 (HTTP {e.code})") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ApiError(f"KOBIS API 연결 실패: {e}") from e
    if "faultInfo" in data:
        fault = data["faultInfo"]
        raise ApiError(f"KOBIS API 오류 {fault.get('errorCode')}: {fault.get('message')}")
    return data


def _int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _pct(cur: float, prev: float) -> float | None:
    return round((cur - prev) / prev * 100, 1) if prev else None


def _direction(change: float | None, threshold: float = 1.0) -> str:
    if change is None:
        return "비교 불가"
    if change > threshold:
        return "상승 ▲"
    if change < -threshold:
        return "하락 ▼"
    return "보합 -"


def _parse_date(text: str | None) -> date:
    """날짜 문자열 -> date. 생략하면 가장 최근에 끝난 주의 일요일."""
    if not text:
        today = date.today()
        return today - timedelta(days=today.weekday() + 1)
    t = text.strip().replace("-", "").replace(".", "").replace("/", "")
    try:
        return datetime.strptime(t, "%Y%m%d").date()
    except ValueError as e:
        raise ValueError(f"날짜 형식이 올바르지 않습니다: '{text}' (예: 20261108 또는 2026-11-08)") from e


def _week_range(d: date) -> tuple[date, date]:
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def _fmt(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _norm_open_dt(v: str | None) -> str | None:
    """'20261105' / '2026-11-05' -> '2026-11-05'"""
    if not v:
        return None
    t = v.strip().replace("-", "")
    return f"{t[:4]}-{t[4:6]}-{t[6:8]}" if len(t) == 8 and t.isdigit() else v.strip() or None


# ---------------------------------------------------------------------------
# KOBIS 데이터 조회
# ---------------------------------------------------------------------------
def _fetch_weekly_boxoffice(d: date, week_gb: int = 0) -> dict[str, Any]:
    """d 가 속한 주의 주간/주말/주중 박스오피스 (상위 10편)."""
    if week_gb not in WEEK_TYPES:
        raise ValueError("week_type 은 0(주간 월~일), 1(주말 금~일), 2(주중 월~목) 중 하나입니다.")
    _, sunday = _week_range(d)
    data = _kobis_get(WEEKLY_BOXOFFICE_URL, {"targetDt": sunday.strftime("%Y%m%d"), "weekGb": week_gb})
    result = data.get("boxOfficeResult", {})
    movies = []
    for m in result.get("weeklyBoxOfficeList", []):
        movies.append({
            "rank": _int(m.get("rank")),
            "rank_change": _int(m.get("rankInten")),
            "is_new": m.get("rankOldAndNew") == "NEW",
            "movie_cd": m.get("movieCd"),
            "title": m.get("movieNm"),
            "open_date": _norm_open_dt(m.get("openDt")),
            "audience": _int(m.get("audiCnt")),
            "audience_change": _int(m.get("audiInten")),
            "audience_change_pct": _float(m.get("audiChange")),
            "audience_acc": _int(m.get("audiAcc")),
            "sales": _int(m.get("salesAmt")),
            "sales_change": _int(m.get("salesInten")),
            "sales_change_pct": _float(m.get("salesChange")),
            "sales_acc": _int(m.get("salesAcc")),
            "sales_share_pct": _float(m.get("salesShare")),  # 전체 극장 매출 대비 점유율
            "screens": _int(m.get("scrnCnt")),
            "shows": _int(m.get("showCnt")),
        })
    raw_range = result.get("showRange", "")
    show_range = " ~ ".join(_norm_open_dt(x) or x for x in raw_range.split("~")) if raw_range else None
    if not movies:
        raise NoDataError(
            f"{_fmt(sunday)} 이 속한 주의 박스오피스 데이터가 없습니다. "
            "아직 집계되지 않은 주(이번 주)이거나 너무 이른 날짜일 수 있습니다."
        )
    return {
        "week": result.get("yearWeekTime"),
        "range": show_range,
        "week_type": WEEK_TYPES[week_gb],
        "movies": movies,
    }


def _resolve_week(target_date: str | None, week_gb: int = 0) -> tuple[date, dict[str, Any], str | None]:
    """조회할 주와 그 주 박스오피스를 정한다.
    날짜를 생략했는데 가장 최근 주가 아직 집계 전이면(예: 월요일 이른 시간) 한 주 전으로 자동 대체한다.
    날짜를 직접 지정했다면 대체하지 않고 오류를 그대로 낸다."""
    d = _parse_date(target_date)
    try:
        return d, _fetch_weekly_boxoffice(d, week_gb), None
    except NoDataError:
        if target_date:
            raise
    monday, sunday = _week_range(d)
    prev = d - timedelta(days=7)
    note = (
        f"가장 최근 주({_fmt(monday)} ~ {_fmt(sunday)})는 아직 KOBIS 집계 전이라 "
        "한 주 전 데이터로 대신 조회했습니다."
    )
    return prev, _fetch_weekly_boxoffice(prev, week_gb), note


_movie_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_movie_cache_lock = threading.Lock()


def _fetch_movies_of_year(year: int) -> list[dict[str, Any]]:
    """해당 연도 개봉(예정)작 전체. KOBIS 는 개봉일을 연도 단위로만 필터링하므로 전 페이지를 받아 캐시한다."""
    with _movie_cache_lock:
        hit = _movie_cache.get(year)
        if hit and time.time() - hit[0] < MOVIE_LIST_CACHE_TTL:
            return hit[1]

    def page(n: int) -> dict[str, Any]:
        return _kobis_get(MOVIE_LIST_URL, {
            "openStartDt": year, "openEndDt": year,
            "itemPerPage": MOVIE_LIST_PAGE_SIZE, "curPage": n,
        })["movieListResult"]

    first = page(1)
    total = _int(first.get("totCnt"))
    pages = max(1, -(-total // MOVIE_LIST_PAGE_SIZE))
    movies = list(first.get("movieList", []))
    if pages > 1:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for res in pool.map(page, range(2, pages + 1)):
                movies.extend(res.get("movieList", []))

    with _movie_cache_lock:
        _movie_cache[year] = (time.time(), movies)
    return movies


# ---------------------------------------------------------------------------
# 도구
# ---------------------------------------------------------------------------
@mcp.tool
def weekly_new_releases(target_date: str | None = None, include_adult: bool = False) -> dict[str, Any]:
    """주차별 개봉 영화를 조회합니다. 지정한 날짜가 속한 주(월~일)에 개봉한 모든 영화를 반환하고,
    그 주 박스오피스 상위 10위 안에 든 작품은 관람객/매출액 성적을 함께 붙입니다.

    Args:
        target_date: 조회할 주에 포함된 아무 날짜 (YYYYMMDD 또는 YYYY-MM-DD). 생략하면 가장 최근에 끝난 주.
        include_adult: 성인물(에로) 장르 포함 여부 (기본 False).
    """
    # 박스오피스 성적 (날짜를 지정했고 아직 집계 전이면 개봉작 목록만 반환)
    boxoffice: dict[str, dict[str, Any]] = {}
    boxoffice_note = fallback_note = None
    if target_date:
        d = _parse_date(target_date)
        try:
            bo_movies = _fetch_weekly_boxoffice(d)["movies"]
        except NoDataError as e:
            bo_movies, boxoffice_note = [], str(e)
    else:
        d, bo, fallback_note = _resolve_week(None)
        bo_movies = bo["movies"]
    for m in bo_movies:
        boxoffice[m["movie_cd"]] = m

    monday, sunday = _week_range(d)
    start, end = _fmt(monday), _fmt(sunday)

    movies: list[dict[str, Any]] = []
    for year in sorted({monday.year, sunday.year}):
        movies.extend(_fetch_movies_of_year(year))

    releases = []
    excluded_adult = 0
    for m in movies:
        open_dt = _norm_open_dt(m.get("openDt"))
        if not open_dt or not (start <= open_dt <= end):
            continue
        genres = m.get("genreAlt") or ""
        if not include_adult and "성인물" in genres:
            excluded_adult += 1
            continue
        item: dict[str, Any] = {
            "title": m.get("movieNm"),
            "title_en": m.get("movieNmEn") or None,
            "open_date": open_dt,
            "status": m.get("prdtStatNm"),
            "nation": m.get("nationAlt") or None,
            "genre": genres or None,
            "directors": [p.get("peopleNm") for p in m.get("directors", [])],
            "movie_cd": m.get("movieCd"),
        }
        bo = boxoffice.get(m.get("movieCd"))
        if bo:
            item["boxoffice"] = {
                "rank": bo["rank"],
                "audience": bo["audience"],
                "audience_acc": bo["audience_acc"],
                "sales": bo["sales"],
                "sales_acc": bo["sales_acc"],
            }
        releases.append(item)

    # 박스오피스 진입작 먼저(순위순), 나머지는 개봉일·제목순
    releases.sort(key=lambda x: (x.get("boxoffice", {}).get("rank", 99), x["open_date"], x["title"] or ""))

    result: dict[str, Any] = {
        "week_range": f"{start} ~ {end}",
        "count": len(releases),
        "in_boxoffice_top10": sum(1 for r in releases if "boxoffice" in r),
        "releases": releases,
    }
    if excluded_adult:
        result["excluded_adult_count"] = excluded_adult
    if boxoffice_note:
        result["boxoffice_note"] = boxoffice_note
    if fallback_note:
        result["fallback_note"] = fallback_note
    return result


def _acc_ranking(target_date: str | None, week_type: int, key: str, label: str) -> dict[str, Any]:
    _, bo, fallback_note = _resolve_week(target_date, week_type)
    ranked =sorted(bo["movies"], key=lambda m: m[key], reverse=True)
    total_acc = sum(m[key] for m in ranked)
    rows = []
    for i, m in enumerate(ranked, 1):
        rows.append({
            "acc_rank": i,
            "weekly_rank": m["rank"],
            "title": m["title"],
            "open_date": m["open_date"],
            key: m[key],
            "share_of_top10_acc_pct": round(m[key] / total_acc * 100, 1) if total_acc else None,
            "this_week": m["audience"] if key == "audience_acc" else m["sales"],
            "is_new": m["is_new"],
        })
    result: dict[str, Any] = {
        "week": bo["week"],
        "range": bo["range"],
        "week_type": bo["week_type"],
        "criterion": label,
        "note": "그 주 박스오피스 상위 10편을 누적값 기준으로 다시 정렬한 순위입니다.",
        "ranking": rows,
    }
    if fallback_note:
        result["fallback_note"] = fallback_note
    return result


@mcp.tool
def weekly_audience_ranking(target_date: str | None = None, week_type: int = 0) -> dict[str, Any]:
    """주차별 누적 관람객 기준 순위. 해당 주 박스오피스 상위 10편을 개봉 후 누적 관람객 수로 정렬합니다.

    Args:
        target_date: 조회할 주에 포함된 아무 날짜 (YYYYMMDD 또는 YYYY-MM-DD). 생략하면 가장 최근에 끝난 주.
        week_type: 0=주간(월~일, 기본), 1=주말(금~일), 2=주중(월~목).
    """
    return _acc_ranking(target_date, week_type, "audience_acc", "누적 관람객 수(명)")


@mcp.tool
def weekly_sales_ranking(target_date: str | None = None, week_type: int = 0) -> dict[str, Any]:
    """주차별 누적 매출액 기준 순위. 해당 주 박스오피스 상위 10편을 개봉 후 누적 매출액(원)으로 정렬합니다.

    Args:
        target_date: 조회할 주에 포함된 아무 날짜 (YYYYMMDD 또는 YYYY-MM-DD). 생략하면 가장 최근에 끝난 주.
        week_type: 0=주간(월~일, 기본), 1=주말(금~일), 2=주중(월~목).
    """
    return _acc_ranking(target_date, week_type, "sales_acc", "누적 매출액(원)")


@mcp.tool
def weekly_trend(target_date: str | None = None, week_type: int = 0) -> dict[str, Any]:
    """주차별 관람객 + 매출액 총합 대비 상승/하락 지표.
    해당 주 상위 10편의 관람객·매출액 총합을 전주 상위 10편 총합과 비교하고,
    영화별로 총합 대비 점유율과 전주 대비 증감(관람객·매출액·순위)을 계산합니다.

    Args:
        target_date: 조회할 주에 포함된 아무 날짜 (YYYYMMDD 또는 YYYY-MM-DD). 생략하면 가장 최근에 끝난 주.
        week_type: 0=주간(월~일, 기본), 1=주말(금~일), 2=주중(월~목).
    """
    d, cur, fallback_note = _resolve_week(target_date, week_type)
    try:
        prev: dict[str, Any] | None = _fetch_weekly_boxoffice(d - timedelta(days=7), week_type)
    except NoDataError:
        prev = None

    movies = cur["movies"]
    audi_total = sum(m["audience"] for m in movies)
    sales_total = sum(m["sales"] for m in movies)

    totals: dict[str, Any] = {
        "audience": {"this_week": audi_total},
        "sales": {"this_week": sales_total},
        # KOBIS salesShare 는 전체 극장 매출 대비 점유율 → 상위 10편 점유율 합
        "top10_share_of_market_sales_pct": round(sum(m["sales_share_pct"] for m in movies), 1),
    }
    if prev:
        p_audi = sum(m["audience"] for m in prev["movies"])
        p_sales = sum(m["sales"] for m in prev["movies"])
        a_pct, s_pct = _pct(audi_total, p_audi), _pct(sales_total, p_sales)
        totals["audience"].update({
            "prev_week": p_audi, "change": audi_total - p_audi,
            "change_pct": a_pct, "direction": _direction(a_pct),
        })
        totals["sales"].update({
            "prev_week": p_sales, "change": sales_total - p_sales,
            "change_pct": s_pct, "direction": _direction(s_pct),
        })
        combined = round((a_pct + s_pct) / 2, 1) if a_pct is not None and s_pct is not None else None
        totals["combined_change_pct"] = combined
        totals["combined_direction"] = _direction(combined)
        totals["prev_week_range"] = prev["range"]
    else:
        totals["note"] = "전주 데이터가 없어 총합 비교를 생략했습니다."

    rows = []
    for m in movies:
        a_share = round(m["audience"] / audi_total * 100, 1) if audi_total else None
        s_share = round(m["sales"] / sales_total * 100, 1) if sales_total else None
        if m["is_new"]:
            combined_pct, trend = None, "신규 진입 ★"
        else:
            combined_pct = round((m["audience_change_pct"] + m["sales_change_pct"]) / 2, 1)
            trend = _direction(combined_pct)
        rows.append({
            "rank": m["rank"],
            "rank_change": m["rank_change"],
            "title": m["title"],
            "audience": m["audience"],
            "audience_share_pct": a_share,
            "audience_change": m["audience_change"],
            "audience_change_pct": None if m["is_new"] else m["audience_change_pct"],
            "sales": m["sales"],
            "sales_share_pct": s_share,
            "sales_change": m["sales_change"],
            "sales_change_pct": None if m["is_new"] else m["sales_change_pct"],
            "combined_share_pct": round((a_share + s_share) / 2, 1) if a_share is not None and s_share is not None else None,
            "combined_change_pct": combined_pct,
            "trend": trend,
        })

    rising = [r["title"] for r in rows if r["trend"].startswith("상승")]
    falling = [r["title"] for r in rows if r["trend"].startswith("하락")]
    new = [r["title"] for r in rows if r["trend"].startswith("신규")]
    result: dict[str, Any] = {
        "week": cur["week"],
        "range": cur["range"],
        "week_type": cur["week_type"],
        "totals": totals,
        "movies": rows,
        "summary": {"rising": rising, "falling": falling, "new_entries": new},
        "notes": [
            "share_pct 는 그 주 상위 10편 총합 대비 비율, combined 는 관람객·매출액 지표의 평균입니다.",
            "총합 비교는 이번 주 상위 10편 합계 vs 전주 상위 10편 합계입니다.",
            "영화별 증감률은 KOBIS 가 제공하는 전주 대비 값이며, 신규 진입작은 비교하지 않습니다.",
        ],
    }
    if fallback_note:
        result["fallback_note"] = fallback_note
    return result


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--http":
        port = int(sys.argv[2]) if len(sys.argv) >= 3 else 8001
        mcp.run(transport="http", host="127.0.0.1", port=port)
    else:
        mcp.run()
