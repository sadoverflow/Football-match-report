# report.py
import os
from typing import Any, Dict, List, Optional, Union

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, timezone

load_dotenv()

Json = Dict[str, Any]


class API:
    __instance = None
    __BASE_URL = "https://api.soccerdataapi.com/"
    __API_KEY = os.getenv("API_KEY")
    __headers = {"Accept-Encoding": "gzip", "Content-Type": "application/json"}

    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = super().__new__(cls)
        return cls.__instance

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.__API_KEY:
            raise RuntimeError("API_KEY is missing in .env")

        query = {"auth_token": self.__API_KEY}
        if params:
            query.update(params)

        url = self.__BASE_URL + endpoint.lstrip("/")
        r = requests.get(url, headers=self.__headers, params=query, timeout=30)

        try:
            data = r.json()
        except Exception:
            raise RuntimeError(f"Invalid JSON response from {url}")

        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {data.get('detail', data)}")

        return data

    def get_match(self, match_id: int) -> Dict[str, Any]:
        return self._get("match/", {"match_id": match_id})

    def get_match_preview(self, match_id: int) -> Dict[str, Any]:
        return self._get("match-preview/", {"match_id": match_id})

    def get_upcoming_matches(self) -> Dict[str, Any]:
        return self._get("match-previews-upcoming/")

    def get_standing(self, league_id: int, season: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"league_id": league_id}
        if season:
            params["season"] = season
        return self._get("standing/", params)

    def get_h2h(self, team_1_id: int, team_2_id: int) -> Dict[str, Any]:
        return self._get("head-to-head/", {"team_1_id": team_1_id, "team_2_id": team_2_id})


api = API()


class BaseX(BaseModel):
    model_config = ConfigDict(extra="allow")


class IDName(BaseX):
    id: int
    name: str


class Team(IDName):
    pass


class Stadium(IDName):
    city: Optional[str] = None


class Stage(IDName):
    is_active: bool


class Goals(BaseX):
    home_ht_goals: int
    away_ht_goals: int
    home_ft_goals: int
    away_ft_goals: int
    home_et_goals: int
    away_et_goals: int
    home_pen_goals: int
    away_pen_goals: int


class Player(BaseX):
    id: int
    name: str
    position: Optional[str] = None
    status: Optional[str] = None
    desc: Optional[str] = None


class LineupPlayer(BaseX):
    player: Player
    position: str


class SidelinedEntry(BaseX):
    player: Player
    status: Optional[str] = None
    desc: Optional[str] = None


class Lineups(BaseX):
    lineup_type: str
    lineups: Dict[str, List[LineupPlayer]]
    bench: Dict[str, List[LineupPlayer]]
    sidelined: Dict[str, List[SidelinedEntry]]
    formation: Dict[str, Union[str, None]]


class OddsMarket(BaseX):
    home: Optional[float] = None
    away: Optional[float] = None
    draw: Optional[float] = None
    total: Optional[float] = None
    over: Optional[float] = None
    under: Optional[float] = None
    market: Optional[Union[str, float]] = None


class Odds(BaseX):
    match_winner: Optional[OddsMarket] = None
    over_under: Optional[OddsMarket] = None
    handicap: Optional[OddsMarket] = None
    last_modified_timestamp: Optional[int] = None


class MatchEvent(BaseX):
    event_type: str
    event_minute: str
    team: str
    player: Optional[Player] = None
    assist_player: Optional[Player] = None
    player_in: Optional[Player] = None
    player_out: Optional[Player] = None


class Weather(BaseX):
    temp_f: float
    temp_c: float
    description: str


class Prediction(BaseX):
    p_type: str = Field(alias="type")
    choice: str
    total: Optional[str] = None


class PreviewData(BaseX):
    weather: Optional[Weather] = None
    excitement_rating: float
    prediction: Optional[Prediction] = None


class DetailedPreview(BaseX):
    word_count: int = 0
    match_data: PreviewData


def _clean_name(x: Any) -> str:
    s = "" if x is None else str(x).strip()
    return "TBD" if s.lower() in ("none", "null", "") else s


def _fmt_num(x: Any) -> str:
    if x is None:
        return "N/A"
    try:
        v = float(x)
        return f"{v:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(x)


def _fmt_temp_c(x: Any) -> str:
    if x is None:
        return "N/A"
    try:
        v = float(x)
        return f"{v:.1f}".rstrip("0").rstrip(".")
    except Exception:
        return str(x)


def _fmt_ts(ts: Optional[int]) -> Optional[str]:
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts), timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return None


def _extract_preview_signals(preview_raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    match_data = preview_raw.get("match_data")
    if not isinstance(match_data, dict):
        return None
    return {
        "word_count": int(preview_raw.get("word_count") or 0),
        "match_data": match_data,
    }


def _standing_lookup(standing_raw: Dict[str, Any], team_id: int) -> Optional[Dict[str, Any]]:
    stages = standing_raw.get("stage")
    if not isinstance(stages, list):
        return None
    for st in stages:
        if not isinstance(st, dict):
            continue
        rows = st.get("standings")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("team_id") == team_id:
                return row
    return None


def _pos_bucket(pos: str) -> str:
    p = (pos or "").strip().lower()
    if "goal" in p or p == "gk":
        return "GK"
    if "def" in p or p == "d":
        return "DEF"
    if "mid" in p or p == "m":
        return "MID"
    if "att" in p or "forw" in p or p == "f":
        return "ATT"
    return "OTHER"


def _format_xi(players: List[LineupPlayer]) -> List[str]:
    buckets: Dict[str, List[str]] = {"GK": [], "DEF": [], "MID": [], "ATT": [], "OTHER": []}
    for lp in players[:11]:
        buckets[_pos_bucket(lp.position)].append(_clean_name(lp.player.name))
    out: List[str] = []
    for k in ["GK", "DEF", "MID", "ATT", "OTHER"]:
        if buckets[k]:
            out.append(f"{k}: " + ", ".join(buckets[k]))
    return out if out else ["XI: Not available"]


def _format_sidelined(title: str, items: List[SidelinedEntry]) -> List[str]:
    if not items:
        return []
    parts: List[str] = []
    for it in items:
        name = _clean_name(it.player.name if it.player else None)
        if name == "TBD":
            continue
        tail: List[str] = []
        if it.status:
            tail.append(str(it.status))
        if it.desc:
            tail.append(str(it.desc))
        parts.append(name if not tail else f"{name} ({'; '.join(tail)})")
    return [f"{title}: " + ", ".join(parts)] if parts else []


class FootballMatch(BaseX):
    id: int
    date: str
    time: str
    country: Optional[IDName] = None
    league: IDName
    stage: Optional[Stage] = None
    teams: Dict[str, Team]
    stadium: Optional[Stadium] = None
    status: str
    minute: int
    winner: str
    has_extra_time: Optional[bool] = None
    has_penalties: Optional[bool] = None
    goals: Goals
    events: List[MatchEvent] = []
    odds: Optional[Odds] = None
    lineups: Optional[Lineups] = None
    match_preview: Optional[Dict[str, Any]] = None
    detailed_preview: Optional[DetailedPreview] = None
    standing: Optional[Dict[str, Any]] = None
    h2h: Optional[Dict[str, Any]] = None

    def generate_report_main(self) -> str:
        home_team = self.teams.get("home")
        away_team = self.teams.get("away")

        home = _clean_name(home_team.name if home_team else None)
        away = _clean_name(away_team.name if away_team else None)

        home_id = home_team.id if home_team else None
        away_id = away_team.id if away_team else None

        league_id = self.league.id
        country = _clean_name(self.country.name if self.country else None)

        r: List[str] = []
        r.append(f"{home} (Home) vs {away} (Away)")
        r.append(f"Match ID: {self.id}")
        r.append(f"Competition: {_clean_name(self.league.name)} ({country}) | League ID: {league_id}")
        if self.stage:
            r.append(f"Stage: {_clean_name(self.stage.name)} | Active: {self.stage.is_active}")
        r.append(f"Kick-off: {self.date} {self.time}")

        if self.stadium:
            venue_name = _clean_name(self.stadium.name)
            venue_city = _clean_name(self.stadium.city)
            r.append(f"Venue: {venue_name}" + (f", {venue_city}" if venue_city != "TBD" else ""))
        else:
            r.append("Venue: TBD")

        r.append("")
        s = (self.status or "").strip().lower()
        if s == "finished":
            r.append("Status: FINISHED")
        elif s == "live":
            r.append(f"Status: LIVE | Minute: {self.minute}")
        else:
            r.append(f"Status: {self.status.upper()}")

        ht = (self.goals.home_ht_goals, self.goals.away_ht_goals)
        ft = (self.goals.home_ft_goals, self.goals.away_ft_goals)
        r.append(f"Score: FT {home} (Home) {ft[0]}–{ft[1]} {away} (Away) | HT {ht[0]}–{ht[1]}")

        w = _clean_name(self.winner)
        if w != "TBD":
            r.append(f"Winner: {w.upper()}")

        if self.detailed_preview:
            md = self.detailed_preview.match_data
            r.append("")
            if md.weather:
                r.append(f"Weather: {_fmt_temp_c(md.weather.temp_c)}°C, {_clean_name(md.weather.description)}")
            r.append(f"Excitement rating: {_fmt_num(md.excitement_rating)}/10")
            if md.prediction:
                ptype = _clean_name(md.prediction.p_type)
                choice = _clean_name(md.prediction.choice)
                total = getattr(md.prediction, "total", None)
                r.append(f"Prediction [{ptype}" + (f" {total}" if total else "") + f"]: {choice}")

        if self.standing and isinstance(self.standing, dict) and home_id and away_id:
            h_row = _standing_lookup(self.standing, int(home_id))
            a_row = _standing_lookup(self.standing, int(away_id))
            if h_row or a_row:
                r.append("")
                r.append("Table snapshot")
                if h_row:
                    r.append(
                        f"{home} (Home): Pos {h_row.get('position')} | Pts {h_row.get('points')} | GP {h_row.get('games_played')} "
                        f"| W-D-L {h_row.get('wins')}-{h_row.get('draws')}-{h_row.get('losses')} "
                        f"| GF {h_row.get('goals_for')} GA {h_row.get('goals_against')}"
                    )
                if a_row:
                    r.append(
                        f"{away} (Away): Pos {a_row.get('position')} | Pts {a_row.get('points')} | GP {a_row.get('games_played')} "
                        f"| W-D-L {a_row.get('wins')}-{a_row.get('draws')}-{a_row.get('losses')} "
                        f"| GF {a_row.get('goals_for')} GA {a_row.get('goals_against')}"
                    )

        if self.odds:
            ts = _fmt_ts(self.odds.last_modified_timestamp)
            r.append("")
            r.append("Odds" + (f" (last update: {ts})" if ts else ""))

            mw = self.odds.match_winner
            if mw and any(v is not None for v in (mw.home, mw.draw, mw.away)):
                r.append(
                    f"1X2: {home} (Home) {_fmt_num(mw.home)} | Draw {_fmt_num(mw.draw)} | {away} (Away) {_fmt_num(mw.away)}"
                )

            ou = self.odds.over_under
            if ou and ou.total is not None and (ou.over is not None or ou.under is not None):
                r.append(f"Over/Under {ou.total}: Over {_fmt_num(ou.over)} | Under {_fmt_num(ou.under)}")

            hc = self.odds.handicap
            if hc and hc.market is not None and (hc.home is not None or hc.away is not None):
                r.append(
                    f"Handicap {hc.market}: {home} (Home) {_fmt_num(hc.home)} | {away} (Away) {_fmt_num(hc.away)}"
                )

        if self.lineups:
            r.append("")
            r.append(f"Lineups: {_clean_name(self.lineups.lineup_type)}")

            h_form = self.lineups.formation.get("home") if isinstance(self.lineups.formation, dict) else None
            a_form = self.lineups.formation.get("away") if isinstance(self.lineups.formation, dict) else None
            if h_form and a_form and str(h_form).lower() != "none" and str(a_form).lower() != "none":
                r.append(f"Formations: {home} (Home) {h_form} | {away} (Away) {a_form}")
            else:
                r.append("Formations: not announced")

            h_xi = self.lineups.lineups.get("home", []) if isinstance(self.lineups.lineups, dict) else []
            a_xi = self.lineups.lineups.get("away", []) if isinstance(self.lineups.lineups, dict) else []

            r.append("")
            r.append(f"{home} (Home) XI")
            r.extend(_format_xi(h_xi))

            r.append("")
            r.append(f"{away} (Away) XI")
            r.extend(_format_xi(a_xi))

            h_side = self.lineups.sidelined.get("home", []) if isinstance(self.lineups.sidelined, dict) else []
            a_side = self.lineups.sidelined.get("away", []) if isinstance(self.lineups.sidelined, dict) else []
            extra: List[str] = []
            extra += _format_sidelined(f"{home} (Home) sidelined", h_side)
            extra += _format_sidelined(f"{away} (Away) sidelined", a_side)
            if extra:
                r.append("")
                r.extend(extra)

        if self.events:
            goals = [e for e in self.events if (e.event_type or "").lower() == "goal"]
            yellows = [e for e in self.events if (e.event_type or "").lower() == "yellow_card"]
            subs = [e for e in self.events if (e.event_type or "").lower() == "substitution"]
            others = [e for e in self.events if e not in goals + yellows + subs]

            r.append("")
            r.append("Events")

            if goals:
                r.append("Goals")
                for e in goals:
                    side = "Home" if (e.team or "").strip().lower() == "home" else "Away"
                    scorer = _clean_name(e.player.name if e.player else None)
                    assist = _clean_name(e.assist_player.name) if e.assist_player else None
                    r.append(f"- {e.event_minute}' {scorer} ({side})" + (f", assist {assist}" if assist else ""))

            if yellows:
                r.append("Yellow cards")
                for e in yellows:
                    side = "Home" if (e.team or "").strip().lower() == "home" else "Away"
                    p = _clean_name(e.player.name if e.player else None)
                    r.append(f"- {e.event_minute}' {p} ({side})")

            if subs:
                r.append("Substitutions")
                for e in subs:
                    side = "Home" if (e.team or "").strip().lower() == "home" else "Away"
                    pin = _clean_name(e.player_in.name if e.player_in else None)
                    pout = _clean_name(e.player_out.name if e.player_out else None)
                    r.append(f"- {e.event_minute}' ({side}) IN {pin} | OUT {pout}")

            if others:
                r.append("Other")
                for e in others[:25]:
                    side = "Home" if (e.team or "").strip().lower() == "home" else "Away"
                    r.append(f"- {e.event_minute}' {e.event_type} ({side})")

        return "\n".join(r)


def _get_complete_match(match_id: int) -> FootballMatch:
    match_raw = api.get_match(match_id)
    if not match_raw or not isinstance(match_raw, dict):
        raise RuntimeError("Empty match response")

    mp = match_raw.get("match_preview") if isinstance(match_raw.get("match_preview"), dict) else None
    has_preview = isinstance(mp, dict) and mp.get("has_preview") is True

    if has_preview:
        preview_raw = api.get_match_preview(match_id)
        extracted = _extract_preview_signals(preview_raw)
        if extracted:
            match_raw["detailed_preview"] = extracted

    league: Any = match_raw.get("league") if isinstance(match_raw.get("league"), dict) else {}
    league_id = league.get("id")
    if league_id:
        try:
            match_raw["standing"] = api.get_standing(int(league_id))
        except Exception:
            pass

    teams: Any = match_raw.get("teams") if isinstance(match_raw.get("teams"), dict) else {}
    home_id = (teams.get("home") or {}).get("id") if isinstance(teams.get("home"), dict) else None
    away_id = (teams.get("away") or {}).get("id") if isinstance(teams.get("away"), dict) else None
    if home_id and away_id and int(home_id) != int(away_id):
        try:
            match_raw["h2h"] = api.get_h2h(int(home_id), int(away_id))
        except Exception:
            pass

    return FootballMatch.model_validate(match_raw)


def build_report_texts(match_id: int) -> str:
    m = _get_complete_match(match_id)
    return m.generate_report_main()
