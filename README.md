# tastytrade-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an autonomous AI agent
(Claude, etc.) connect to **Tastytrade** — scan markets, build option strategies,
inspect accounts/positions/orders, and (optionally) place and manage trades.

- **OAuth2** authentication via the official [`tastytrade`](https://github.com/tastyware/tastytrade)
  Python SDK (session tokens auto-refresh; refresh tokens are long-lived).
- **Credentials stored in the OS keyring** (Windows Credential Manager / DPAPI,
  macOS Keychain, Linux Secret Service) — never in files, never in env vars, never logged.
- **Live trading is gated** behind `ENABLE_LIVE_TRADING` — disabled by default so
  an agent cannot place real orders without an explicit opt-in.
- **stdio** transport by default; optional **HTTP** transport hardened with CORS
  and per-IP rate limiting.

## Install

```bash
pip install -e .          # or: pip install -e .[dev] for tests
```

## 1. Create a Tastytrade OAuth application

1. In the Tastytrade web app, open **Manage → My Profile → API → OAuth Applications**.
2. Create an application, select the scopes you need (read + trading), and add
   `http://localhost:8000` as a valid redirect/callback URI.
3. Save the **client secret** (shown once).
4. Create a **grant** to obtain a long-lived **refresh token**.

> Never paste secrets into code, `.env`, or version control.

## 2. Store credentials in the keyring

```bash
tastytrade-mcp secrets set
tastytrade-mcp secrets status
```

You'll be prompted (hidden input) for the client secret, refresh token, and an
optional default account number. `secrets status` also shows which keyring backend
is active — useful for diagnosing credential issues on a new machine.

### Headless Linux (servers, Docker, CI)

Desktop Linux uses GNOME Keyring or KWallet. On headless systems (no desktop
daemon) the native backend is unavailable. Install the encrypted-file fallback:

```bash
pip install 'tastytrade-mcp[headless]'
export PYTHON_KEYRING_BACKEND=keyrings.alt.file.EncryptedKeyring
tastytrade-mcp secrets set
```

`EncryptedKeyring` stores secrets in `~/.local/share/python_keyring/cryptedpass.cfg`
encrypted with a master password you set on first use. Keep this file out of
version control. `PYTHON_KEYRING_BACKEND` selects the backend only; it is not a secret.

## 3. Configure

Copy `.env.example` to `.env` and adjust. Key flags:

| Variable | Default | Meaning |
|---|---|---|
| `ENABLE_LIVE_TRADING` | `false` | Register order-placing tools |
| `FORCE_DRY_RUN` | `false` | Force all orders to dry-run (propose-only mode) |
| `BUYING_POWER_BUFFER_PCT` | `0` | Percent of buying power always kept in reserve (per order) |
| `ACCOUNT_DEPLOY_LIMIT_PCT` | `0` | Account-wide cap on deployed buying power (from live positions) |
| `MCP_CORS_ORIGIN` | `http://localhost:3333` | Allowed CORS origin (HTTP transport) |
| `MCP_RATE_LIMIT` | `120/minute` | Per-IP rate limit (HTTP transport) |
| `MCP_HTTP_HOST` / `MCP_HTTP_PORT` | `127.0.0.1` / `7698` | HTTP bind address |

## 4. Run

```bash
tastytrade-mcp                    # stdio (default)
tastytrade-mcp --transport http   # HTTP, CORS + rate limited
```

### Connect an agent (stdio)

Claude Desktop / Claude Code MCP config:

```json
{
  "mcpServers": {
    "tastytrade": {
      "command": "tastytrade-mcp"
    }
  }
}
```

## Tools

**Always available (read-only):**
`get_connection_status`, `get_market_overview`, `get_option_chain`,
`get_strategies`, `get_account_info`, `get_positions`, `list_accounts`,
`get_working_orders`, `get_watchlists`.

**Only when `ENABLE_LIVE_TRADING=true`:**
`execute_trade`, `adjust_order`, `close_position`, `manage_watchlist`.
Order tools default to `dry_run=true` (validate without submitting).

### Underlying last price

`get_market_overview` now includes a **`last`** field (most recent trade price
from DXLink) alongside the IV metrics for each symbol. Pass it directly as
`around_price` in `get_strategies` and `get_option_chain`:

```jsonc
get_market_overview({ "symbols": ["XSP"] })
// -> { "ok": true, "metrics": [{ "symbol": "XSP", "last": 736.55,
//      "implied_volatility_index_rank": "0.48", ... }] }
```

`last` is streamed in parallel with the metrics fetch (best-effort, 4 s timeout).
It is omitted per symbol when the DXLink feed is unavailable — fall back to the
ATM strike from `get_option_chain` in that case.

### Futures options

Both `get_option_chain` and `get_strategies` work with futures-options underlyings.
Pass the futures root symbol prefixed with `/`:

```jsonc
get_option_chain({ "symbol": "/ES", "expiration": "2026-06-27",
                   "include_greeks": true, "around_price": 5650.0 })

get_strategies({ "symbol": "/ES", "target_dte": 0, "short_delta": 0.10,
                 "wing_width": 25, "around_price": 5650.0 })
```

The response shape is identical to equity options. `instrument_type` on each leg
will be `"Future Option"` instead of `"Equity Option"` — use the correct value
when constructing order legs for `execute_trade`. `contract_multiplier` in the
`get_strategies` response reflects the option-to-futures ratio (typically `1.0`);
the *dollar* value per point depends on the underlying futures contract's own
multiplier (e.g. $50/point for `/ES`, $5/point for `/MES`) — verify before sizing.

### Delta-based iron condor construction

`get_strategies` builds a complete iron condor candidate with live credit and POP
estimates. Pass `around_price` (the underlying's last price from `get_market_overview`)
so strike selection uses **live greeks** from the DXLink feed — without it, the
tool cannot center its greeks window and falls back to a positional heuristic
that will pick the wrong strikes on large chains (e.g. XSP, SPX):

```jsonc
get_strategies({
  "symbol": "XSP",
  "target_dte": 0,
  "short_delta": 0.15,
  "wing_width": 5,
  "around_price": 738.50    // always pass — required for delta-accurate strikes
})
// -> {
//   "ok": true,
//   "strategy": "iron_condor",
//   "net_credit": 1.20,
//   "net_credit_per_contract": 120.0,
//   "estimated_pop": 0.70,
//   "quotes_complete": true,
//   "greeks_used_for_strike_selection": true,   // false = fell back to heuristic
//   "legs": {
//     "short_put":  { "strike_price": "736", "symbol": "XSP...P736", ... },
//     "long_put":   { "strike_price": "731", "symbol": "XSP...P731", ... },
//     "short_call": { "strike_price": "739", "symbol": "XSP...C739", ... },
//     "long_call":  { "strike_price": "744", "symbol": "XSP...C744", ... }
//   }
// }
```

**`greeks_used_for_strike_selection`** — check this field before acting on the
result. When `false`, the greeks feed was unavailable and the tool fell back to
picking the lower/upper third of the full strike list, which is rarely the right
delta on a large chain. Cross-check the returned strikes against `get_option_chain`
deltas before entry.

**`quotes_complete`** — when `false`, `net_credit` is `null` (the DXLink quote
feed was temporarily unavailable). Do not estimate credit from strike prices;
retry next iteration.

### Per-strike greeks on the option chain

`get_option_chain` returns instrument fields per strike by default. Pass
`include_greeks=true` to merge live **`delta`, `gamma`, `theta`, and `iv`**
(annualized implied volatility) into each strike — useful for verifying strike
placement, detecting put/call skew from per-strike IV, and assessing 0DTE gamma
risk:

```jsonc
get_option_chain({
  "symbol": "XSP",
  "expiration": "2026-06-20",   // recommended with greeks (bounds the fetch)
  "include_greeks": true,
  "strike_count": 15,           // ATM window: 15 strikes each side (default); null = full chain
  "around_price": 581.40,       // center on the underlying's last price
  "greeks_timeout": 6.0
})
// -> chain[expiration] = [
//   { "strike_price": "580", "option_type": "Put", "symbol": "...",
//     "streamer_symbol": ".XSP...", "delta": -0.18, "gamma": 0.042,
//     "theta": -0.95, "iv": 0.187 }, ... ]
```

Greeks come from the **DXLink streaming feed** (not the chain endpoint), so this
adds latency and is opt-in. With `include_greeks` and no `expiration`, the tool
defaults to the **nearest** expiration to bound the subscription. An ATM window
is applied by default (`strike_count` defaults to **15** strikes each side of the
money, centered on `around_price` or the median strike) to keep the subscription
small and fast; pass `strike_count: null` for the full chain. If the feed is slow
or unavailable, the chain is still returned and `greeks_complete` / `greeks_received`
report coverage rather than failing.

### Order safety layers

A live order requires **all** of the following, so it cannot happen by accident:

1. `ENABLE_LIVE_TRADING=true` — otherwise the order tools are not registered at all.
2. `FORCE_DRY_RUN=false` — when set to `true`, every order is forced to dry-run
   regardless of what the agent requests ("propose-only" mode).
3. The agent explicitly passes `dry_run=false` on the call.

Before any submission, `execute_trade` / `adjust_order` run a **pre-flight
dry-run** and validate **buying power**: the order is rejected if it would leave
projected buying power below the required reserve (`BUYING_POWER_BUFFER_PCT` of
current buying power; with the default `0` it only blocks orders that would go
negative), or if the API returns errors. Rejections return
`{"ok": false, "error": "pre-flight validation failed", "problems": [...]}` and
nothing is submitted. The projected buying-power effect — including
`required_reserve` and `buffer_pct` when a buffer is set — is returned on every
call under `"buying_power"`.

**Account-wide deployment cap.** `ACCOUNT_DEPLOY_LIMIT_PCT` adds a ceiling on
total deployed buying power (vs. the per-order `BUYING_POWER_BUFFER_PCT`). It is
**derived from live account state** — `used_derivative_buying_power` vs.
`derivative_buying_power` — not an in-memory counter. Capacity = used + available;
the limit is that percent of capacity, and an order is rejected if it would push
deployed buying power past it. Because it reads the account each time, it counts
buying power consumed by *existing* positions (even ones this server didn't
place) and stays correct across restarts and multiple server instances. The
figures (`account_deployed_current`, `account_deployed_after`,
`account_deploy_limit`, `account_buying_power_capacity`) appear in the
`"buying_power"` block.

## Safety

- **Account numbers are masked in logs** to the last 4 digits (`****1234`);
  secrets are never logged.
- **HTTP transport** restricts CORS to a single configured origin and rate-limits
  to 120 requests/minute per IP (HTTP 429 when exceeded).

## Development

```bash
pip install -e .[dev]
pytest                       # unit + integration tests (SDK mocked, no network)
pytest --cov                 # with coverage report
```

### Live integration tests

A separate, opt-in suite hits the real Tastytrade API using your stored
credentials to confirm the SDK + OAuth + API contract works end-to-end. It is
skipped by default and never submits a real order (the test server runs with
`force_dry_run=true`). To run it:

```bash
tastytrade-mcp secrets set     # if not already stored
RUN_LIVE=1 pytest -m live -v
```

Tests that depend on endpoints which are intermittently unavailable (e.g.
`/market-metrics`) skip themselves on a 5xx rather than failing.

## Disclaimer

This software can place real orders against a live brokerage account. It is
provided **"as is", with no warranty**, and is **not financial advice**. You are
solely responsible for any trades it places and any resulting losses. Review the
order-safety controls above before enabling live trading. The built-in risk checks
reduce — but do not eliminate — the risk of an unintended or oversized order.

This is an independent project and is **not affiliated with, endorsed by, or
sponsored by tastytrade**. It uses the unofficial third-party
[`tastytrade`](https://github.com/tastyware/tastytrade) SDK.

## License

[MIT](LICENSE) © 2026 Jon Covington
