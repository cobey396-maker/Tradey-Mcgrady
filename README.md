# V7.1 — frozen snapshot (2026-07-20)

The best-validated configuration found so far. This folder is a **frozen copy** —
`config.yaml` in the project root may drift as you keep experimenting; this one
does not. Restore it any time with:

```bash
cp strategies/v7.1/config_v7.1.yaml config.yaml
```

---

## The strategy in one paragraph

**Opening Range Breakout.** Mark the high and low of the first 30 minutes of the
RTH session (09:30–10:00 ET). When price closes beyond that range (either
direction), take the trade. Stop goes on the **full opposite side of the range** —
if price crosses the entire range against you, the breakout clearly failed. Target
is **0.75R**. One breakout per instrument per day, everything flat by 15:55.
(The config also carries a breakeven step at +1R — see the note below: it can
never fire behind a 0.75R target, so the measured results are from a system with
no breakeven step at all.)

## Exact settings

| Parameter | Value |
|---|---|
| Strategy | ORB (opening range breakout) |
| Timeframe | 5-minute |
| Opening range | 09:30–10:00 (30 min) |
| Entry | close beyond range ± 1 tick |
| Direction | both long and short |
| Stop | full opposite side of range |
| Target | 0.75R |
| Breakeven step | configured at +1R — **never fires**, see note |
| Frequency | 1 breakout per instrument per day |
| Instruments | MES, MNQ, M2K, MGC, MCL, MYM |
| **Risk per trade** | **0.25%** ($125 on $50k) |
| **Max concurrent** | **3** (portfolio-wide) |
| Session | 09:35–15:55, flat by close |
| Daily loss limit | $1,000 |
| Trailing drawdown | $2,000 (Topstep 50k) |
| Costs modelled | $1.24/contract/side + 1 tick slippage |

## Measured results (60 days real 5m data, 6 instruments, one shared $50k account)

```
Trades:          87            Win rate:       67.8%
Profit factor:   1.63          Avg R:         +0.16R
Net P&L:      +$1,428          Total return:   +2.86%
Max drawdown:    0.69%         Verdict:        SURVIVED
```

Period breakdown (fresh $50k each, ~12 days per period):

| Period | Dates | Net P&L | Win% | PF |
|---|---|---|---|---|
| 1 | May 1–15 | −$54 | 55.0% | 0.91 |
| 2 | May 15–Jun 1 | +$205 | 64.7% | 1.39 |
| 3 | Jun 1–14 | +$265 | 71.4% | 1.61 |
| 4 | Jun 14–29 | +$352 | 76.9% | 2.15 |
| 5 | Jun 29–Jul 13 | +$661 | 73.9% | 2.51 |

**4 of 5 periods positive.**

---

## ⚠️ What this is NOT

**It is not proven, and it should not be funded on this evidence.**

- **60 days of data only.** One market regime. A genuinely good strategy and a
  lucky one look identical at this sample size.
- **Selected after testing many variants** on this same window. Some of the edge
  you see is selection bias — that's unavoidable when you search.
- **Did not pass the evaluation**: +$1,428 against a $3,000 profit target.
- **The high win rate is structurally fragile.** At a 0.75R target you need
  **57.1%** wins just to break even. The measured 67.8% leaves ~10 points of
  margin, and real fills are worse than modelled — slippage eats that margin
  directly. Small winners + high win rate flips negative faster than it feels.

## Why the settings are what they are (hard-won)

- **Max 3 concurrent, not 6** — 6 concurrent at 0.5% risk **killed the account in
  4 days**. Correlated index futures all lose together; concurrency was the single
  biggest account-killer found.
- **0.25% risk** — with 3 concurrent that's 0.75% total exposure, comfortably
  inside the $1,000 daily limit.
- **Full-range stop, not half** — a half-range stop sat inside the normal retest
  noise: 13% win rate *and* oversized positions (tighter stop buys more contracts).
- **0.75R target, not 2R** — these breakouts make a modest move then revert. 0.75R
  beat 1R/1.5R/2R/3R on win rate, P&L, expectancy **and** consistency.
- **Breakeven at 1R, not at a small $ amount** — a dollar-based breakeven at ~0.2R
  scratched winners and measurably hurt results. ⚠️ **But the 1R step is
  unreachable**: open profit cannot reach 1R because the 0.75R target fills first.
  The step never fires, so every measured result above came from a system with no
  breakeven step. A breakeven step has therefore never actually been tested on
  this strategy — see [docs/FREQUENT_TRADING.md](docs/FREQUENT_TRADING.md) §4.
- **Daily loss limit ON** — with it off there is no circuit breaker, and losses
  compound into a trailing-drawdown termination.

## How to run it

**Backtest:**
```bash
cd ~/daytrading-bot
.venv/bin/python -c "
import logging
from src.config import load_config
from src.data.loader import load_csv
from src.backtest.portfolio import PortfolioEngine
from src.logging_setup import setup_logging
setup_logging(level=logging.CRITICAL); logging.disable(logging.ERROR)
cfg=load_config('config.yaml'); tz='America/New_York'
SYMS=['MES','MNQ','M2K','MGC','MCL','MYM']
data={s: load_csv(f'data/real_{s}_5m.csv', tz=tz) for s in SYMS}
e=PortfolioEngine(cfg,data); print(e.run().pretty()); print(e.prop_report().pretty())
"
```

**Forward-test in TradingView:** paste `orb_strategy_v7.pine` into the Pine
Editor, add to a 5-minute chart, open the Strategy Tester.
⚠️ Pine runs one chart at a time — it **cannot** model the shared balance, the
3-position cap, or the account-level trailing drawdown. The Python engine is the
authority on risk.

**Live demo (Tradovate):** use `orb_alerts_v7.pine` + `run_demo_server.py`.
See `tradingview/README.md`.

## Monitoring it

```bash
python run_dashboard.py --demo     # try the UI with synthetic data
python run_dashboard.py            # read output/trades.csv + config.yaml
```

A read-only web dashboard at <http://127.0.0.1:8080>: equity against the
prop firm's trailing-drawdown floor, friction in R and the win rate it demands,
the realised-R distribution, rolling win rate against breakeven, daily P&L
against the loss limit, and the blotter. See [docs/DASHBOARD.md](docs/DASHBOARD.md).

## Trading it more often

`config_v8_scalp.yaml` is an **unvalidated** higher-frequency variant: 5-minute
opening range, up to 3 breakouts per instrument per day, 1.0R target, 0.15% risk.
It buys frequency with more setups per day rather than tighter stops, because
friction is charged per trade — a 10-tick MES stop pays ~0.40R in costs and needs
a 70% win rate at a 1R target just to break even. The reasoning, the cost tables,
and the sequence of tests that would falsify it are in
[docs/FREQUENT_TRADING.md](docs/FREQUENT_TRADING.md). **It has never been run.**

## The one thing that would settle it

Multi-year intraday data. `data/fetch_databento.py` and `data/fetch_polygon.py`
are built and waiting on an API key. Everything above is provisional until this
runs across several years and market regimes.
