import os
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from report import api, FootballMatch


Json = Dict[str, Any]


@dataclass(frozen=True)
class BotSettings:
    token: str
    allowed_league_ids: Tuple[int, ...]
    upcoming_limit_per_league: int = 20


DEFAULT_LEAGUE_IDS = (
    228, 326, 310, 322, 323, 198, 235, 241, 253, 297, 299, 168
)


def _load_settings() -> BotSettings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is missing")

    ids_env = os.getenv("LEAGUE_IDS", "").strip()
    if ids_env:
        ids: List[int] = []
        for x in ids_env.split(","):
            x = x.strip()
            if x.isdigit():
                ids.append(int(x))
        allowed = tuple(ids) if ids else DEFAULT_LEAGUE_IDS
    else:
        allowed = DEFAULT_LEAGUE_IDS

    limit_env = os.getenv("UPCOMING_LIMIT_PER_LEAGUE", "").strip()
    limit = int(limit_env) if limit_env.isdigit() else 20

    return BotSettings(token=token, allowed_league_ids=allowed, upcoming_limit_per_league=limit)


def _safe_get(obj: Any, path: List[str], default: Any = None) -> Any:
    cur = obj
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _parse_upcoming(resp: Any) -> List[Dict[str, Any]]:
    if not isinstance(resp, dict):
        return []

    results = resp.get("results")
    if not isinstance(results, list):
        return []

    leagues: List[Dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue

        league_id = item.get("league_id")
        league_name = item.get("league_name")
        country_name = _safe_get(item, ["country", "name"])

        previews = item.get("match_previews")
        if not isinstance(previews, list):
            continue

        matches: List[Dict[str, Any]] = []
        for p in previews:
            if not isinstance(p, dict):
                continue
            mid = p.get("id")
            if not isinstance(mid, int):
                continue

            matches.append(
                {
                    "match_id": mid,
                    "date": p.get("date"),
                    "time": p.get("time"),
                    "home_id": _safe_get(p, ["teams", "home", "id"]),
                    "away_id": _safe_get(p, ["teams", "away", "id"]),
                    "home_name": _safe_get(p, ["teams", "home", "name"]),
                    "away_name": _safe_get(p, ["teams", "away", "name"]),
                }
            )

        leagues.append(
            {
                "league_id": league_id,
                "league_name": league_name,
                "country_name": country_name,
                "matches": matches,
            }
        )

    return leagues


def _key_dt(m: Dict[str, Any]) -> Tuple[str, str, int]:
    d = str(m.get("date") or "")
    t = str(m.get("time") or "")
    mid = int(m.get("match_id") or 0)
    return (d, t, mid)


def _chunks(text: str, limit: int = 3900) -> List[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    out: List[str] = []
    buf: List[str] = []
    cur = 0

    for line in text.splitlines():
        add = (len(line) + 1) if buf else len(line)
        if cur + add > limit and buf:
            out.append("\n".join(buf).strip())
            buf = [line]
            cur = len(line)
        else:
            buf.append(line)
            cur += add

    if buf:
        out.append("\n".join(buf).strip())
    return [x for x in out if x]


def _build_report_without_preview(match_id: int) -> str:
    match_raw = api.get_match(match_id)
    if not isinstance(match_raw, dict) or not match_raw:
        raise RuntimeError("Empty match payload")

    league_id = _safe_get(match_raw, ["league", "id"])
    if isinstance(league_id, int):
        try:
            match_raw["standing"] = api.get_standing(int(league_id))
        except Exception:
            pass

    home_id = _safe_get(match_raw, ["teams", "home", "id"])
    away_id = _safe_get(match_raw, ["teams", "away", "id"])
    if isinstance(home_id, int) and isinstance(away_id, int) and home_id != away_id:
        try:
            match_raw["h2h"] = api.get_h2h(int(home_id), int(away_id))
        except Exception:
            pass

    match = FootballMatch.model_validate(match_raw)
    return match.generate_full_report()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = ", ".join(str(x) for x in context.application.bot_data["settings"].allowed_league_ids)
    txt = (
        "Commands:\n"
        "/upcoming — upcoming matches (filtered)\n\n"
        f"Allowed league_ids: {allowed}"
    )
    await update.message.reply_text(txt)


async def cmd_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s: BotSettings = context.application.bot_data["settings"]
    allowed = set(s.allowed_league_ids)

    try:
        resp = api.get_upcoming_matches()
    except Exception as e:
        await update.message.reply_text(f"API error: {e}")
        return

    leagues = _parse_upcoming(resp)
    leagues = [x for x in leagues if isinstance(x.get("league_id"), int) and x["league_id"] in allowed]
    leagues.sort(key=lambda x: int(x.get("league_id") or 10**9))

    if not leagues:
        await update.message.reply_text("No upcoming matches for configured leagues.")
        return

    for lg in leagues:
        league_id = int(lg["league_id"])
        league_name = str(lg.get("league_name") or "Unknown League")
        country = str(lg.get("country_name") or "").strip()
        header = f"{league_id} — {league_name}" + (f" ({country})" if country else "")
        await update.message.reply_text(header)

        matches = lg.get("matches") or []
        matches = sorted(matches, key=_key_dt)[: s.upcoming_limit_per_league]

        if not matches:
            await update.message.reply_text("No matches in this league block.")
            continue

        for m in matches:
            mid = int(m["match_id"])
            home = str(m.get("home_name") or "TBD")
            away = str(m.get("away_name") or "TBD")
            date = str(m.get("date") or "TBD")
            time_ = str(m.get("time") or "TBD")
            stage = league_name

            body = (
                f"{home} (Home) vs {away} (Away)\n"
                f"Date/Time: {date} {time_}\n"
                f"League: {league_name} | League ID: {league_id}\n"
                f"Stage: {stage}\n"
                f"Match ID: {mid}"
            )

            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Get report", callback_data=f"report:{mid}")]]
            )
            await update.message.reply_text(body, reply_markup=kb)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return

    data = (q.data or "").strip()
    if not data.startswith("report:"):
        await q.answer()
        return

    try:
        match_id = int(data.split(":", 1)[1])
    except Exception:
        await q.answer("Bad match id")
        return

    await q.answer("Building report...")
    try:
        text = _build_report_without_preview(match_id)
    except Exception as e:
        await q.message.reply_text(f"Report error: {e}")
        return

    for part in _chunks(text, limit=3900):
        await q.message.reply_text(part)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            ("upcoming", "Upcoming matches (filtered)"),
            ("start", "Help"),
        ]
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = _load_settings()

    app = Application.builder().token(settings.token).post_init(post_init).build()
    app.bot_data["settings"] = settings

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("upcoming", cmd_upcoming))
    app.add_handler(CallbackQueryHandler(on_callback))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
