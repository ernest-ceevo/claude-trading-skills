# Alpaca Market-Data Provider (fork)

This fork (ernest-ceevo/claude-trading-skills) adds an optional **Alpaca
market-data provider** to the shared `fmp_client.py` transport so the price
surface runs without a Financial Modeling Prep key.

## How it works

`scripts/fmp_client/core_template.py.tmpl` (rendered into every
`skills/<skill>/scripts/fmp_client.py` by `scripts/generate_fmp_client.py`)
now resolves a provider at client construction:

| `DATA_SOURCE` | `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` | Resolved provider |
| --- | --- | --- |
| `alpaca` | any | Alpaca (FMP fallback if `FMP_API_KEY` is set) |
| `fmp` | any | FMP only |
| unset/`auto` | set | **Alpaca** (FMP fallback if `FMP_API_KEY` is set) |
| unset/`auto` | unset | FMP (key required, unchanged upstream behavior) |

Requests still follow the same FMP-v3 response shapes, so the consuming
skills are unchanged: `historical` returns
`{"symbol": ..., "historical": [...]}` (rows most-recent-first, exactly
`timeseries=N` rows), and `quote` returns the FMP quote list with
`price`/`previousClose`/`dayHigh`/`dayLow`/`volume`/`changesPercentage`.

## Coverage

| Endpoint | Alpaca | Notes |
| --- | --- | --- |
| `historical` (daily bars) | yes | `data.alpaca.markets/v2/stocks/bars`, paginated, `adjustment=raw`; `adjClose` equals raw close (no dividend adjustment) |
| `quote` | yes | `v2/stocks/snapshots`; price = last trade or daily-bar close; volume from the daily bar |
| earnings calendar, economic calendar, COT, 13F, S&P 500 constituents, company screener, dividends, fundamentals | no | No Alpaca equivalent. `DATA_SOURCE=alpaca` without `FMP_API_KEY` fails fast with a clear error for these; skills degrade to their documented manual/CSV fallbacks |

# Environment variables

- `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` - enable Alpaca (auto-detected)
- `ALPACA_DATA_FEED` - `iex` (default, free plans) or `sip` (paid plans;
  full consolidated-tape volume). With `iex`, daily-bar volume is
  IEX-only and underreports consolidated volume.
- `DATA_SOURCE` - `alpaca` / `fmp` / `auto` (unset)
- `MARKET_DATA_ADAPTER` - full path to the shared machine-wide market-data
  adapter module (default install:
  `/mnt/shared/hermes/market-data-adapter/market_data_adapter.py`).
  When set, bars/quotes delegate to it (including its cache) and the
  embedded Alpaca implementation below stays as the fallback. Used to
  share one data layer between multiple repos on the machine.
- `MARKET_CACHE_PATH` - SQLite daily-bar cache database (e.g.
  `/mnt/shared/hermes/market-data/bars.db`). When set, daily-bar
  requests are served read-through: symbols fully covered by cached,
  current data come back with zero network calls; only stale or
  uncovered symbols are fetched (gap fill) and upserted. Quotes are
  never cached. All cache failures fail open to the network path.

## Market cache (SQLite)

| Property | Value |
| --- | --- |
| Store | `MARKET_CACHE_PATH` SQLite file, schema `daily_bars(symbol, date, o/h/l/c, adj_close, volume, source, fetched_at)` |
| Concurrency | WAL journal, 5s busy timeout - safe for parallel readers across sessions |
| Freshness rule | hit requires ≥N rows in window AND newest bar ≥ expected latest completed ET session (bars count after ~17:00 ET; holidays self-heal by refetch) |
| Write path | `INSERT OR REPLACE` upserts after every provider fetch; the full fetched window is stored, not just the requested row count |
| Failure mode | fail-open - any cache error logs a warning and falls back to the provider |

Measured on a 250-row SPY pull: cold 0.93s (1 network call), warm 0.00s
(0 network calls), rows identical.

## Caveats

- Alpaca symbols use dots (`BRK.B`); FMP-style dashes (`BRK-B`) are mapped
  automatically. Index symbols (`^GSPC`) have no Alpaca equivalent and
  return `None` with a clear `_last_error`.
- `sp500_constituents` consumers (e.g. `vcp-screener` default screening)
  still need FMP or must run with an explicit `--universe`.
- Skills whose whole transport is a standalone template (`canslim-screener`,
  `macro-regime-detector`, `market-top-detector`,
  `us-undervalued-growth-screener`) remain FMP-only.
- `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` reads are audited by
  `scripts/check_api_requirements.py` and declared as `alpaca: optional`
  integrations in `skills-index.yaml`.
