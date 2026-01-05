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
    matches_per_message: int = 10
    buttons_per_row: int = 2


DEFAULT_LEAGUE_IDS = (228, 326, 310, 322, 323, 198, 235, 241, 253, 297, 299, 168)


def _load_settings() -> BotSettings:
    token = os.getenv("TG_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TG_BOT_TOKEN is missing")

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
    upcoming_limit_per_league = int(limit_env) if limit_env.isdigit() else 20

    mpm_env = os.getenv("MATCHES_PER_MESSAGE", "").strip()
    matches_per_message = int(mpm_env) if mpm_env.isdigit() else 10
    matches_per_message = max(1, min(25, matches_per_message))

    bpr_env = os.getenv("BUTTONS_PER_ROW", "").strip()
    buttons_per_row = int(bpr_env) if bpr_env.isdigit() else 2
    buttons_per_row = max(1, min(4, buttons_per_row))

    return BotSettings(
        token=token,
        allowed_league_ids=allowed,
        upcoming_limit_per_league=upcoming_limit_per_league,
        matches_per_message=matches_per_message,
        buttons_per_row=buttons_per_row,
    )


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
    text = (text or "").strip()
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


def _strip_preview_lines(report_text: str) -> str:
    lines = (report_text or "").splitlines()
    kept: List[str] = []
    for ln in lines:
        if ln.strip().startswith("Preview:"):
            continue
        kept.append(ln)

    cleaned: List[str] = []
    blank = False
    for ln in kept:
        if ln.strip() == "":
            if not blank:
                cleaned.append("")
            blank = True
        else:
            cleaned.append(ln)
            blank = False

    return "\n".join(cleaned).strip()


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
    text = match.generate_report_main()
    return _strip_preview_lines(text)


def _keyboard_for_match_ids(match_ids: List[int], buttons_per_row: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for mid in match_ids:
        row.append(InlineKeyboardButton(f"Report {mid}", callback_data=f"r:{mid}"))
        if len(row) >= buttons_per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _format_league_block_header(league_id: int, league_name: str, country: str, part_idx: int, total_parts: int) -> str:
    base = f"[{league_id}] {league_name}".strip()
    if country:
        base += f" ({country})"
    if total_parts > 1:
        base += f" — part {part_idx}/{total_parts}"
    return base


def _format_matches_block(matches: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for m in matches:
        mid = int(m.get("match_id") or 0)
        home = str(m.get("home_name") or "TBD")
        away = str(m.get("away_name") or "TBD")
        date = str(m.get("date") or "TBD")
        time_ = str(m.get("time") or "TBD")
        lines.append(f"- {home} (Home) vs {away} (Away) | {date} {time_} | Match ID: {mid}")
    return "\n".join(lines).strip()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s: BotSettings = context.application.bot_data["settings"]
    allowed = ", ".join(str(x) for x in s.allowed_league_ids)
    txt = (
        "Commands:\n"
        "/upcoming — upcoming matches (grouped by league)\n"
        "/report <match_id> — get report for a match\n\n"
    )
    await update.message.reply_text(txt)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not context.args or not str(context.args[0]).isdigit():
        await update.message.reply_text("Usage: /report <match_id>")
        return

    match_id = int(context.args[0])
    try:
        text = _build_report_without_preview(match_id)
    except Exception as e:
        await update.message.reply_text(f"Report error: {e}")
        return

    for part in _chunks(text, limit=3900):
        await update.message.reply_text(part)


async def cmd_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

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

        matches_all = lg.get("matches") or []
        matches_all = sorted(matches_all, key=_key_dt)[: s.upcoming_limit_per_league]

        if not matches_all:
            continue

        per_msg = s.matches_per_message
        batches: List[List[Dict[str, Any]]] = [
            matches_all[i : i + per_msg] for i in range(0, len(matches_all), per_msg)
        ]

        total_parts = len(batches)
        for idx, batch in enumerate(batches, start=1):
            header = _format_league_block_header(league_id, league_name, country, idx, total_parts)
            body = _format_matches_block(batch)
            text = f"{header}\n{body}".strip()

            match_ids = [int(m["match_id"]) for m in batch if isinstance(m.get("match_id"), int)]
            kb = _keyboard_for_match_ids(match_ids, buttons_per_row=s.buttons_per_row)

            await update.message.reply_text(text, reply_markup=kb)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return

    data = (q.data or "").strip()
    if not data.startswith("r:"):
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
        if q.message:
            await q.message.reply_text(f"Report error: {e}")
        return

    if q.message:
        for part in _chunks(text, limit=3900):
            await q.message.reply_text(part)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            ("upcoming", "Upcoming matches (grouped by league)"),
            ("report", "Get report by match id"),
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
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CallbackQueryHandler(on_callback))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
