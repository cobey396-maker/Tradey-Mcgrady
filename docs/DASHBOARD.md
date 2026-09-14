# Dashboard

A read-only web monitor for the bot. It observes; it never places, sizes,
modifies or cancels an order, and it never writes to `config.yaml` or the
blotter. (`tests/test_dashboard.py::test_no_route_can_mutate_anything` asserts
every route is a GET, so that stays true.)

## Run it

```bash
pip install -r requirements.txt

python run_dashboard.py --demo          # synthetic data — nothing else needed
python run_dashboard.py                 # reads output/trades.csv + config.yaml
python run_dashboard.py --config config_v8_scalp.yaml --blotter output/v8.csv
```

Then open <http://127.0.0.1:8080>.

It binds to **localhost** by default. Your blotter and your prop-firm headroom
are not things to put on `0.0.0.0` without thinking about who else is on the
network.

## Where the data comes from

| Source | Path | Produced by |
|---|---|---|
| Trade blotter | `output/trades.csv` | `run_backtest.py` (and the portfolio engine) |
| Config | `config.yaml` | you |
| Open positions | `output/live_state.json` | the execution layer — **optional** |

The blotter is re-read whenever its mtime changes, so a running backtest shows up
without restarting the server. A half-written row is skipped rather than crashing
the page.

`live_state.json` is optional and nothing writes it yet. When it is absent the UI
says *"execution layer is not reporting state"* rather than showing zero open
positions — "I don't know" and "flat" are different answers, and only one of them
is safe to act on. To wire it up, have the execution layer write:

```json
{
  "as_of": "2026-07-15T11:42:00",
  "halted": false,
  "reason": null,
  "open_positions": [
    {"symbol": "MNQ", "side": "long", "contracts": 2, "entry_price": 20115.25,
     "stop": 20081.75, "target": 20148.75, "unrealized": 41.0,
     "opened_at": "2026-07-15T11:12:00"}
  ]
}
```

## What it shows, and why those things

Most trading dashboards lead with P&L. P&L is the least actionable number on the
page — it tells you what already happened. These four panels tell you what is
about to happen:

**Equity vs the trailing-drawdown floor.** The red line is the prop firm's
trailing maximum-loss floor, reconstructed with Topstep's rules: it trails up on
*end-of-day* balance only, never moves down, and locks once it reaches the
starting balance. Touching it ends the account. A rising equity curve that stays
close to its floor is still a fragile account, and only this chart shows that.
*Caveat:* it is built from closed trades, so it cannot see unrealized P&L — real
breaches are checked on live equity, so true headroom is always ≤ what is drawn.

**Cost of trading — the frequency tax.** Friction expressed in R, and the win
rate it demands. Commission and slippage are charged per *trade*, not per dollar
risked, so this is the first panel to look at on any higher-frequency variant —
see [FREQUENT_TRADING.md](FREQUENT_TRADING.md). If "% of gross edge" is over 50%,
the broker is the majority shareholder in the strategy.

**Realised R distribution.** Should be two spikes: one at the target, one at −1R.
Bars to the left of −1R are stop slippage — risk you believed was capped and
wasn't. They are drawn at full opacity so they are hard to miss.

**Rolling 20-trade win rate** against the breakeven-with-friction line. When the
blue line crosses below the orange one, the system is losing money by
construction, not by bad luck. Degradation shows up here before it shows up in
the equity curve.

Plus: daily P&L against the daily loss limit, per-instrument and per-hour
breakdowns, exit-reason tallies, and the full blotter. Filter by symbol and date;
refreshes every 15 seconds.

## Design notes

No build step, no CDN, no JS dependencies — the charts are drawn straight onto
`<canvas>`. A dashboard for a trading bot should start on a machine with no
outbound network, and one that depends on a CDN doesn't.

Stats are computed in `dashboard/metrics.py` from stdlib only (no pandas), so
the whole data layer is unit-testable in milliseconds.

## Layout

```
dashboard/
  instruments.py   contract specs — ticks to dollars, round-turn friction
  models.py        Trade + blotter CSV parsing
  store.py         config/blotter/live-state reading, mtime cache, demo data
  metrics.py       all statistics, incl. the prop-firm floor reconstruction
  app.py           FastAPI routes
  static/          index.html, app.js, styles.css
run_dashboard.py   entry point
tests/test_dashboard.py
```
