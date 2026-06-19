# tastytrade-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an autonomous AI agent
(Claude, etc.) connect to **Tastytrade** — scan markets, build option strategies,
inspect accounts/positions/orders, and (optionally) place and manage trades.

- **OAuth2** authentication via the official [`tastytrade`](https://github.com/tastyware/tastytrade)
  Python SDK (session tokens auto-refresh; refresh tokens are long-lived).
- **Credentials stored in the OS keyring** (Windows Credential Manager / DPAPI,
  macOS Keychain, Linux Secret Service) — never in files, never in env vars,
  never logged. This mirrors the [meic-trader](https://github.com/joncovington/meic-trader)
  pattern.
- **Tool surface modeled on**
  [TastyScanner-MCP-Server](https://github.com/technet365/TastyScanner-MCP-Server).
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
4. Create a **grant** to obtain a long-lived **refresh token**. For sandbox you
   can instead run `from tastytrade.oauth import login; login(is_test=True)`.

> Never paste secrets into code, `.env`, or version control.

## 2. Store credentials in the keyring

```bash
tastytrade-mcp secrets set --sandbox      # or --production
tastytrade-mcp secrets status --sandbox
```

You'll be prompted (hidden input) for the client secret, refresh token, and an
optional default account number.

## 3. Configure

Copy `.env.example` to `.env` and adjust. Key flags:

| Variable | Default | Meaning |
|---|---|---|
| `TASTYTRADE_SANDBOX` | `false` | Use the sandbox/cert environment |
| `TASTYTRADE_MOCK` | `false` | Serve simulated SDK responses (no creds, no network) |
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
      "command": "tastytrade-mcp",
      "env": { "TASTYTRADE_SANDBOX": "true" }
    }
  }
}
```

## Mock mode (test without credentials)

Run the server with `TASTYTRADE_MOCK=true` to serve **simulated SDK responses** —
no credentials, no network, orders never submitted. An agent (or you) can
exercise every tool against deterministic data: connection checks, account and
market queries, strategy building, and dry-run order placement.

```bash
TASTYTRADE_MOCK=true ENABLE_LIVE_TRADING=true tastytrade-mcp
```

Equivalent startup **flags** (handy when an agent launches the server itself):

```bash
tastytrade-mcp --mock --enable-live-trading
tastytrade-mcp --mock --mock-fixture examples/mock_fixture.json --enable-live-trading
```

Flags override the matching env vars (`--mock` → `TASTYTRADE_MOCK`,
`--mock-fixture` → `TASTYTRADE_MOCK_FIXTURE`, `--enable-live-trading` →
`ENABLE_LIVE_TRADING`, `--sandbox` → `TASTYTRADE_SANDBOX`); `--mock-fixture`
implies `--mock`.

Or in your MCP client config — either via `env` or `args`:

```jsonc
{
  "servers": {
    "tastytrade-mock": {
      "type": "stdio",
      "command": "tastytrade-mcp",
      "args": ["--mock", "--enable-live-trading"]
    }
  }
}
```

`get_connection_status` reports `"mock_mode": true` so an agent can tell it is
talking to the simulator. This is the recommended way for an agent to validate
its request/response handling before pointing at the sandbox or production.

### Custom mock scenarios

Point `TASTYTRADE_MOCK_FIXTURE` at a JSON file to override the simulated data —
custom account number, balances, positions, working orders, market metrics, and
error/outage injection:

```bash
TASTYTRADE_MOCK=true TASTYTRADE_MOCK_FIXTURE=examples/mock_fixture.json tastytrade-mcp
```

All sections are optional (omitted ones use defaults). See
[examples/mock_fixture.json](examples/mock_fixture.json). Highlights:

- `balances`, `positions`, `working_orders`, `account_number` — shape the account.
- `order_response.errors` — make `execute_trade` get **rejected** by pre-flight.
- `raise: { "get_positions": "502 Bad Gateway" }` — simulate an **endpoint outage**.
- `option_chain` — define exact expirations/strikes/symbols (ISO-date keys) so
  `get_option_chain` / `get_strategies` return a chain you control; omit it to get
  a procedurally generated chain.
- The buying-power figures feed the real risk checks, so you can reproduce buffer
  / deployment-limit rejections deterministically.

### Example: mid-day MEIC scenario

The shipped [examples/mock_fixture.json](examples/mock_fixture.json) is a ready-made
scenario modeled on [MEICAgent](https://github.com/joncovington/MEICAgent) — a
Multiple Entry Iron Condor bot that opens several 0DTE iron condors through the
day and protects each with a break-even stop. Loading it drops an agent into a
realistic **mid-session** state:

- **3 open XSP 0DTE iron condors = 12 position legs.** Each condor is a short
  call spread + short put spread, wing width 5, ~0.15-delta shorts, 1 contract,
  entered at progressively shifted strikes (as the underlying drifted through the
  morning).
- **3 break-even stop-limit working orders**, one per condor, sized near the
  credit received — mirroring MEIC's "DAY stop-limit at break-even, tightened as
  the day progresses" behavior.
- **Realistic balances** — ~$1,200 of derivative buying power used by the three
  condors, the rest available — so `get_account_info`, `get_positions`,
  `get_working_orders`, and the deployment-cap check all return believable data.
- **IV metrics and an option chain** so the agent can evaluate a 4th entry, plus
  an `order_response` describing that prospective entry's dry-run buying-power
  effect.

Run it:

```bash
tastytrade-mcp --mock --mock-fixture examples/mock_fixture.json --enable-live-trading
```

An agent then sees the exact decision point MEICAgent faces each ~5-minute loop:
*three positions and three stops are open — add a fourth condor, tighten stops,
or hold?* — and can rehearse that logic with zero credentials, zero network, and
no possibility of a real order.

> **Caveat — the dates are illustrative, not live 0DTE.** The position option
> symbols carry a fixed expiration (`260618`) and the `option_chain` uses a
> far-future placeholder key (`2099-01-15`). These are **display/structural
> stand-ins**: position symbols are just strings the tools echo back, and the
> chain's exact date only matters to `get_strategies`' nearest-DTE selection.
> For a faithful 0DTE simulation that exercises that selection against *today*,
> edit the fixture and set the `option_chain` date key (and, if you care about
> the displayed leg symbols, the `260618` in each position symbol) to the current
> date. Nothing else in the scenario depends on the date. This fixture validates
> the **request/response contract and the agent's decision logic** — not market
> realism, live greeks, or fills.

### Scenario variants

Alongside the default, four variant fixtures reproduce specific MEIC decision and
limit branches so an agent can be tested against each in isolation:

| Fixture | Agent behavior under test |
|---|---|
| [`mock_fixture_stop_filled.json`](examples/mock_fixture_stop_filled.json) | **Step 4a** — a stop is missing from working orders (it filled), triggering post-stop evaluation. 12 legs on book, only 2 of 3 stops live. |
| [`mock_fixture_stale_pending.json`](examples/mock_fixture_stale_pending.json) | **Step 4b** — a pending entry older than 10 min triggers cancellation. Includes a `Received` order with a far-past `received_at`. |
| [`mock_fixture_bp_rejection.json`](examples/mock_fixture_bp_rejection.json) | **Step 6** — `execute_trade` dry-run returns a buying-power rejection (projected BP goes negative); the agent should skip the entry. |
| [`mock_fixture_mcp_outage.json`](examples/mock_fixture_mcp_outage.json) | **Hard limit 7** — `get_connection_status` fails (account call raises); the agent should log and halt. |

```bash
tastytrade-mcp --mock --mock-fixture examples/mock_fixture_stop_filled.json --enable-live-trading
```

The same date caveat above applies to every variant.

## Tools

**Always available (read-only):**
`get_connection_status`, `get_market_overview`, `get_option_chain`,
`get_strategies`, `get_account_info`, `get_positions`, `list_accounts`,
`get_working_orders`, `get_watchlists`.

**Only when `ENABLE_LIVE_TRADING=true`:**
`execute_trade`, `adjust_order`, `close_position`, `manage_watchlist`.
Order tools default to `dry_run=true` (validate without submitting).

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

- Defaults to **sandbox + live-trading-off**.
- **Account numbers are masked in logs** to the last 4 digits (`****1234`);
  secrets are never logged.
- HTTP transport restricts CORS to a single origin and rate-limits to
  120 requests/minute per IP (HTTP 429 when exceeded).

## Development

```bash
pip install -e .[dev]
pytest                       # unit tests (SDK mocked, no network)
```

### Live sandbox integration tests

A separate, opt-in suite hits the real Tastytrade **sandbox** using your stored
credentials to confirm the SDK + OAuth + API contract works end-to-end. It is
skipped by default and never submits a real order (the test server runs with
`force_dry_run=true`). To run it:

```bash
tastytrade-mcp secrets set --sandbox     # if not already stored
RUN_LIVE_SANDBOX=1 pytest -m live -v
```

Tests that depend on cert endpoints which are intermittently unavailable (e.g.
`/market-metrics`) skip themselves on a 5xx rather than failing.

## Disclaimer

This software can place real orders against a live brokerage account. It is
provided **"as is", with no warranty**, and is **not financial advice**. You are
solely responsible for any trades it places and any resulting losses. Test in the
sandbox (`TASTYTRADE_SANDBOX=true`) before enabling live trading, and review the
order-safety controls above. The built-in risk checks reduce — but do not
eliminate — the risk of an unintended or oversized order.

This is an independent project and is **not affiliated with, endorsed by, or
sponsored by tastytrade**. It uses the unofficial third-party
[`tastytrade`](https://github.com/tastyware/tastytrade) SDK.

## License

[MIT](LICENSE) © 2026 Jon Covington
