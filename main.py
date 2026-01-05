import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, HTTPException, Query
from dotenv import load_dotenv

load_dotenv()

Json = Dict[str, Any]


class SoccerDataApi:
    def __init__(self) -> None:
        self.base_url = "https://api.soccerdataapi.com/"
        self.api_key = os.getenv("API_KEY")
        self.headers = {"Accept-Encoding": "gzip", "Content-Type": "application/json"}

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if not self.api_key:
            raise RuntimeError("API_KEY is missing in .env")

        query: Dict[str, Any] = {"auth_token": self.api_key}
        if params:
            query.update(params)

        url = self.base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        r = requests.get(url, headers=self.headers, params=query, timeout=30)

        try:
            data = r.json()
        except Exception:
            raise RuntimeError(f"Invalid JSON response from {url}")

        if r.status_code >= 400:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            raise RuntimeError(f"HTTP {r.status_code}: {detail}")

        return data

    def match(self, match_id: int) -> Any:
        return self._get("match/", {"match_id": match_id})

    def match_preview(self, match_id: int) -> Any:
        return self._get("match-preview/", {"match_id": match_id})

    def standing(self, league_id: int, season: Optional[str] = None) -> Any:
        params: Dict[str, Any] = {"league_id": league_id}
        if season:
            params["season"] = season
        return self._get("standing/", params)

    def h2h(self, team_1_id: int, team_2_id: int) -> Any:
        return self._get("head-to-head/", {"team_1_id": team_1_id, "team_2_id": team_2_id})

    def upcoming(self) -> Any:
        return self._get("match-previews-upcoming/")

    def matches(self, league_id: Optional[int] = None, season: Optional[str] = None, date: Optional[str] = None) -> Any:
        params: Dict[str, Any] = {}
        if league_id is not None:
            params["league_id"] = league_id
        if season:
            params["season"] = season
        if date:
            params["date"] = date
        return self._get("matches/", params)


api = SoccerDataApi()
app = FastAPI(title="FB Dataset API", version="1.0.0")


def _safe_get(obj: Any, path: List[Any], default: Any = None) -> Any:
    cur = obj
    for k in path:
        if isinstance(k, int):
            if not isinstance(cur, list) or k >= len(cur):
                return default
            cur = cur[k]
        else:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
    return cur


def _parse_dt(date_s: Optional[str], time_s: Optional[str]) -> Optional[datetime]:
    if not date_s or not time_s:
        return None
    d = str(date_s).strip()
    t = str(time_s).strip()
    fmts = ["%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M"]
    for f in fmts:
        try:
            dt = datetime.strptime(f"{d} {t}", f)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _extract_preview(preview_raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(preview_raw, dict):
        return None
    match_data = preview_raw.get("match_data")
    content = preview_raw.get("content") or preview_raw.get("preview_content")
    if not isinstance(match_data, dict) or not isinstance(content, list) or not content:
        return None
    return {
        "word_count": int(preview_raw.get("word_count") or 0),
        "match_data": match_data,
        "content": content,
    }


def _standing_row(standing_raw: Any, team_id: int) -> Optional[Dict[str, Any]]:
    stages = _safe_get(standing_raw, ["stage"])
    if not isinstance(stages, list):
        return None
    for st in stages:
        rows = st.get("standings") if isinstance(st, dict) else None
        if not isinstance(rows, list):
            continue
        for r in rows:
            if isinstance(r, dict) and r.get("team_id") == team_id:
                return r
    return None


def _implied_probs_1x2(mw: Any) -> Dict[str, Any]:
    if not isinstance(mw, dict):
        return {"available": False}
    h = mw.get("home")
    d = mw.get("draw")
    a = mw.get("away")
    if not all(isinstance(x, (int, float)) and x > 1e-9 for x in [h, d, a]):
        return {"available": False}
    ih, idr, ia = 1.0 / float(h), 1.0 / float(d), 1.0 / float(a)
    s = ih + idr + ia
    if s <= 0:
        return {"available": False}
    return {
        "available": True,
        "home": ih / s,
        "draw": idr / s,
        "away": ia / s,
        "overround": s,
    }


def _implied_probs_ou(ou: Any) -> Dict[str, Any]:
    if not isinstance(ou, dict):
        return {"available": False}
    total = ou.get("total")
    over = ou.get("over")
    under = ou.get("under")
    if not (isinstance(over, (int, float)) and isinstance(under, (int, float)) and over > 1e-9 and under > 1e-9):
        return {"available": False}
    io, iu = 1.0 / float(over), 1.0 / float(under)
    s = io + iu
    if s <= 0:
        return {"available": False}
    return {
        "available": True,
        "total": total,
        "over": io / s,
        "under": iu / s,
        "overround": s,
    }


def _handicap_available(hc: Any) -> Dict[str, Any]:
    if not isinstance(hc, dict):
        return {"available": False}
    if hc.get("market") is None:
        return {"available": False}
    if hc.get("home") is None and hc.get("away") is None:
        return {"available": False}
    return {"available": True, "market": hc.get("market")}


def _bundle(match_raw: Dict[str, Any], standing_season: Optional[str]) -> Dict[str, Any]:
    match = match_raw if isinstance(match_raw, dict) else {}
    league_id = _safe_get(match, ["league", "id"])
    home_id = _safe_get(match, ["teams", "home", "id"])
    away_id = _safe_get(match, ["teams", "away", "id"])

    mp = match.get("match_preview") if isinstance(match.get("match_preview"), dict) else {}
    has_preview = mp.get("has_preview") is True

    preview: Optional[Dict[str, Any]] = None
    if has_preview:
        try:
            pr = api.match_preview(int(match["id"]))
            preview = _extract_preview(pr)
        except Exception:
            preview = None

    standing: Optional[Any] = None
    standing_rows = {"home": None, "away": None}
    if isinstance(league_id, int):
        try:
            standing = api.standing(int(league_id), season=standing_season)
            if isinstance(home_id, int):
                standing_rows["home"] = _standing_row(standing, int(home_id))
            if isinstance(away_id, int):
                standing_rows["away"] = _standing_row(standing, int(away_id))
        except Exception:
            standing = None
            standing_rows = {"home": None, "away": None}

    h2h: Optional[Any] = None
    if isinstance(home_id, int) and isinstance(away_id, int) and int(home_id) != int(away_id):
        try:
            h2h = api.h2h(int(home_id), int(away_id))
        except Exception:
            h2h = None

    dt = _parse_dt(match.get("date"), match.get("time"))
    kickoff_iso = dt.isoformat() if dt else None

    status = str(match.get("status") or "").strip().lower()
    hft = _safe_get(match, ["goals", "home_ft_goals"])
    aft = _safe_get(match, ["goals", "away_ft_goals"])
    label = None
    if status == "finished" and isinstance(hft, int) and isinstance(aft, int) and hft >= 0 and aft >= 0:
        label = int(hft + aft)

    odds = match.get("odds") if isinstance(match.get("odds"), dict) else {}
    mw = odds.get("match_winner")
    ou = odds.get("over_under")
    hc = odds.get("handicap")

    h2h_overall = _safe_get(h2h, ["stats", "overall"]) if isinstance(h2h, dict) else None
    avg_goals = None
    if isinstance(h2h_overall, dict):
        gp = h2h_overall.get("overall_games_played")
        s1 = h2h_overall.get("overall_team1_scored")
        s2 = h2h_overall.get("overall_team2_scored")
        try:
            if gp and float(gp) > 0:
                avg_goals = (float(s1 or 0) + float(s2 or 0)) / float(gp)
        except Exception:
            avg_goals = None

    features = {
        "match_id": match.get("id"),
        "status": match.get("status"),
        "kickoff": {"date": match.get("date"), "time": match.get("time"), "iso": kickoff_iso},
        "teams": {"home": match.get("teams", {}).get("home"), "away": match.get("teams", {}).get("away")},
        "league": match.get("league"),
        "preview": {
            "has_preview": bool(has_preview),
            "word_count": mp.get("word_count"),
            "excitement_rating": mp.get("excitement_rating"),
        },
        "odds": {
            "implied_1x2": _implied_probs_1x2(mw),
            "implied_over_under": _implied_probs_ou(ou),
            "handicap": _handicap_available(hc),
        },
        "standing_snapshot": {
            "home": standing_rows["home"],
            "away": standing_rows["away"],
            "season": standing_season,
        },
        "h2h": {
            "overall": h2h_overall,
            "avg_goals_per_game": avg_goals,
        },
        "label": label,
    }

    return {
        "match": match,
        "preview": preview,
        "standing": standing,
        "standing_rows": standing_rows,
        "h2h": h2h,
        "features": features,
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/raw/match/{match_id}")
def raw_match(match_id: int) -> Any:
    try:
        return api.match(match_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/raw/match-preview/{match_id}")
def raw_match_preview(match_id: int) -> Any:
    try:
        return api.match_preview(match_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/raw/standing")
def raw_standing(league_id: int = Query(...), season: Optional[str] = Query(None)) -> Any:
    try:
        return api.standing(league_id, season=season)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/raw/h2h")
def raw_h2h(team_1_id: int = Query(...), team_2_id: int = Query(...)) -> Any:
    try:
        return api.h2h(team_1_id, team_2_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/raw/upcoming")
def raw_upcoming() -> Any:
    try:
        return api.upcoming()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/raw/matches")
def raw_matches(
    league_id: Optional[int] = Query(None),
    season: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
) -> Any:
    try:
        return api.matches(league_id=league_id, season=season, date=date)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/dataset/match/{match_id}")
def dataset_match(match_id: int, standing_season: Optional[str] = Query(None)) -> Any:
    try:
        m = api.match(match_id)
        return _bundle(m, standing_season=standing_season)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/dataset/matches")
def dataset_matches(
    match_ids: List[int] = Query(...),
    standing_season: Optional[str] = Query(None),
) -> Any:
    bundles: List[Dict[str, Any]] = []
    for mid in match_ids:
        try:
            m = api.match(int(mid))
            bundles.append(_bundle(m, standing_season=standing_season))
        except Exception:
            continue
    return {"bundles": bundles}


@app.get("/dataset/upcoming")
def dataset_upcoming(league_id: Optional[int] = Query(None)) -> Any:
    try:
        upcoming = api.upcoming()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    out: List[Dict[str, Any]] = []
    if isinstance(upcoming, list):
        for item in upcoming:
            if not isinstance(item, dict):
                continue
            if league_id is not None:
                lid = _safe_get(item, ["league", "id"])
                if lid != league_id:
                    continue
            out.append(item)

    return {"items": out}


@app.get("/dataset/season-matches")
def dataset_season_matches(
    league_id: int = Query(...),
    season: str = Query(...),
    only_finished: bool = Query(True),
    limit: Optional[int] = Query(None),
) -> Any:
    try:
        raw = api.matches(league_id=league_id, season=season)
        print(raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    match_items: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for block in raw:
            matches = block.get("matches") if isinstance(block, dict) else None
            if isinstance(matches, list):
                for m in matches:
                    if isinstance(m, dict):
                        match_items.append(m)

    if only_finished:
        match_items = [m for m in match_items if str(m.get("status") or "").strip().lower() == "finished"]

    if limit is not None and limit > 0:
        match_items = match_items[: int(limit)]

    match_ids: List[int] = []
    for m in match_items:
        mid = m.get("id")
        if isinstance(mid, int):
            match_ids.append(mid)

    return {"league_id": league_id, "season": season, "count": len(match_ids), "match_ids": match_ids, "matches": match_items}