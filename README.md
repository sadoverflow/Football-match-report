# Football Analytics Telegram Bot

This project is a Telegram bot that provides structured information about upcoming football matches and detailed match reports using the SoccerDataAPI.

The bot is designed as an analytics and research tool and can be extended for betting models, data analysis, or machine learning pipelines.

---

## Features

### Upcoming Matches

- Command: `/upcoming`
- Fetches upcoming matches from SoccerDataAPI
- Filters matches by predefined league IDs
- Groups matches by league
- Sorts leagues by league ID
- Displays matches in a clean, structured format:
  - Home team vs Away team
  - Date and time
  - League name
  - Competition stage
  - Match ID
- Each match includes an inline button to request a detailed report

---

### Match Report

- Triggered via inline button (`Get report`)
- Fetches full match data by match ID
- Generates a comprehensive text report including:
  - Match metadata (teams, league, stage, venue, kickoff)
  - Match status and score (if available)
  - Standings snapshot (if available)
  - Head-to-head statistics
  - Odds (1X2, Over/Under, Handicap) when available
  - Lineups, formations, bench, sidelined players
  - Match events (goals, cards, substitutions)

Preview text content is intentionally excluded to keep reports concise and focused on structured data.

---

## Tech Stack

- Python
- Telegram Bot API
- Requests
- Pydantic
- SoccerDataAPI

---

## Configuration

Environment variables required:

- `API_KEY` — SoccerDataAPI key
- `BOT_TOKEN` — Telegram bot token

---

## Notes

- The bot currently supports a single command flow.
- League filtering is hardcoded and can be easily adjusted.
- The project is structured to be extended with:
  - Machine learning models
  - Dataset generation
  - Betting analytics
  - Additional Telegram commands

---

## Disclaimer

This project is for educational and analytical purposes only.
It does not provide betting advice or guarantees.
