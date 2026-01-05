# main.py
import os
import asyncio
import logging
from typing import Any, Dict, List, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from report import api, build_report_texts

ALLOWED_LEAGUE_IDS = {228, 326, 310, 322, 323, 198, 235, 241, 253, 297, 299, 168}


def _chunk_text(s: str, limit: int = 3800) -> List[str]:
    s = (s or "").strip()
    if not s:
        return []
    if len(s) <= limit:
        return [s]
    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for line in s.splitlines():
        add = len(line) + 1
        if cur and cur_len + add > limit:
            chunks.append("\n".join(cur).strip())
            cur = [line]
            cur_len = len(line) + 1
        else:
            cur.append(line)
            cur_len += add
    if cur:
        chunks.append("\n".join(cur).strip())
    return [c for c in chunks if c]


def _flatten_upcoming(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = payload.get("results")
    if not isinstance(results, list):
        return []

    out: List[Dict[str, Any]] = []
    for league_block in results:
        if not isinstance(league_block, dict):
            continue

        league_id = league_block.get("league_id")
        if not isinstance(league_id, int) or league_id not in ALLOWED_LEAGUE_IDS:
            continue

        league_name = str(league_block.get("league_name") or "")
        country = league_block.get("country") if isinstance(league_block.get("country"), dict) else {}
        country_name = str(country.get("name") or "")

        previews = league_block.get("match_previews")
        if not isinstance(previews, list):
            continue

        for m in previews:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if not isinstance(mid, int):
                continue
            teams = m.get("teams") if isinstance(m.get("teams"), dict) else {}
            home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
            away = teams.get("away") if isinstance(teams.get("away"), dict) else {}

            out.append(
                {
                    "league_id": league_id,
                    "league_name": league_name,
                    "country_name": country_name,
                    "match_id": mid,
                    "date": str(m.get("date") or ""),
                    "time": str(m.get("time") or ""),
                    "home": str(home.get("name") or "TBD"),
                    "away": str(away.get("name") or "TBD"),
                }
            )

    return out


def _group_by_league(items: List[Dict[str, Any]]) -> List[Tuple[int, List[Dict[str, Any]]]]:
    by: Dict[int, List[Dict[str, Any]]] = {}
    for it in items:
        by.setdefault(int(it["league_id"]), []).append(it)

    for lid in by:
        by[lid].sort(key=lambda x: (x.get("date") or "", x.get("time") or "", x.get("match_id") or 0))

    return sorted(by.items(), key=lambda kv: kv[0])


def _build_league_message(league_id: int, matches: List[Dict[str, Any]]) -> str:
    league_name = matches[0].get("league_name") or ""
    country_name = matches[0].get("country_name") or ""

    header = f"{league_name} ({country_name}) | League ID: {league_id}"
    lines: List[str] = [header, ""]
    for m in matches:
        lines.append(f"• {m['home']} (Home) vs {m['away']} (Away) | {m.get('date') or 'TBD'} {m.get('time') or 'TBD'} | ID: {m['match_id']}")
    return "\n".join(lines)


def _build_league_keyboard(matches: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    buttons: List[InlineKeyboardButton] = [
        InlineKeyboardButton(text=f"Report {m['match_id']}", callback_data=f"r:{m['match_id']}") for m in matches
    ]
    rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])
    return InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Commands: /upcoming")


async def cmd_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        payload = await asyncio.to_thread(api.get_upcoming_matches)
    except Exception as e:
        await update.message.reply_text(f"API error: {e}")
        return

    flat = _flatten_upcoming(payload)
    grouped = _group_by_league(flat)

    if not grouped:
        await update.message.reply_text("No upcoming matches for configured leagues.")
        return

    for league_id, matches in grouped:
        text = _build_league_message(league_id, matches)
        kb = _build_league_keyboard(matches)

        chunks = _chunk_text(text, limit=3800)
        if not chunks:
            continue

        if len(chunks) == 1:
            await update.message.reply_text(chunks[0], reply_markup=kb)
        else:
            for c in chunks[:-1]:
                await update.message.reply_text(c)
            await update.message.reply_text(chunks[-1], reply_markup=kb)


async def on_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return

    data = (q.data or "").strip()
    if not data.startswith("r:"):
        return

    try:
        match_id = int(data.split(":", 1)[1])
    except Exception:
        await q.answer("Invalid match id", show_alert=True)
        return

    await q.answer("Building report...")

    try:
        text = await asyncio.to_thread(build_report_texts, match_id)
    except Exception as e:
        await q.message.reply_text(f"Report error for {match_id}: {e}")
        return

    for part in _chunk_text(text, limit=3900):
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

    token = (os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("TG_BOT_TOKEN is missing in .env")

    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("upcoming", cmd_upcoming))
    app.add_handler(CallbackQueryHandler(on_report_callback, pattern=r"^r:\d+$"))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
