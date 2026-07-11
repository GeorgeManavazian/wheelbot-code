# ThetaData v3 access (discovered 2026-07-11)

Local terminal (Java): `~/ThetaTerminal/ThetaTerminalv3u.jar`, launched with
`PATH=/opt/homebrew/opt/openjdk/bin:$PATH java -jar ThetaTerminalv3u.jar`
(reads `THETADATA_API_KEY` from `.env` beside the jar). Serves REST on
**http://127.0.0.1:25503**. Subscription: **Options STANDARD, Stock FREE**, 4 concurrent.

Full tool schema: `docs/thetadata-v3-tools.json` (66 tools, from the terminal's MCP at /mcp/sse).

## Conventions
- REST path = `/v3/` + MCP tool name with `_`→`/`. e.g. tool `option_history_greeks_eod` → `/v3/option/history/greeks/eod`.
- Responses are **CSV**. Dates **ISO** `YYYY-MM-DD`. Auth is implicit (terminal holds the key).
- v2 renames: `root→symbol`, `ivl→interval`. SPY option history back to **2012**.

## Wheel-critical endpoints (all confirmed 200 with real SPY data)
- `/v3/option/list/expirations?symbol=SPY` → all expiries (CSV symbol,expiration).
- `/v3/option/list/strikes?symbol=SPY&expiration=2024-01-19`
- **`/v3/option/history/greeks/eod?symbol=SPY&expiration=<ISO>&start_date=<ISO>&end_date=<ISO>&strike_range=<n>`**
  → whole-chain daily EOD WITH greeks. Columns incl: strike, right, close, bid, ask, **delta**, gamma, theta, vega, implied_vol, underlying_price. **THE strike-selection + marking endpoint.**
- `/v3/option/history/eod?...` → chain EOD prices/quotes (no greeks).
- `/v3/option/history/ohlc?...&interval=60000` and `/v3/option/history/greeks/all?...&interval=` → INTRADAY (TP layer later).
- Optional filters: `strike`, `right`, `strike_range` (band around ATM), `max_dte`.
