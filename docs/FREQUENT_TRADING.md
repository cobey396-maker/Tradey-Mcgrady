# Trading more often: what it costs and what it needs

`config_v8_scalp.yaml` is a higher-frequency variant of V7.1. This document is
the reasoning behind it. **Nothing in here has been backtested** — it is
arithmetic plus V7.1's measured results, and it exists so the first sweep tests
a hypothesis instead of a hunch.

---

## 1. The one equation that governs this

Commission and slippage are charged **per trade**, not per dollar risked. Trade
twice as often and you pay twice the tax, while the edge per trade is unchanged.
Express that tax in units of R — the planned risk on the trade — and call it `f`:

```
f = round_turn_cost_per_contract / risk_per_contract
```

With a target of `m` R, the win rate you need just to break even is:

```
p × (m − f)  =  (1 − p) × (1 + f)        (win nets m − f, loss costs 1 + f)

                1 + f
    =>   p  =  ───────
                1 + m
```

At `f = 0` this is the familiar `1 / (1 + m)`. Every unit of friction raises the
bar — and **it raises it more for a small target than a large one**:

| Target | f = 0 | f = 0.10 | f = 0.20 | f = 0.40 |
|---|---|---|---|---|
| 0.75R | 57.1% | **62.9%** | 68.6% | 80.0% |
| 1.0R  | 50.0% | **55.0%** | 60.0% | 70.0% |
| 1.5R  | 40.0% | 44.0% | 48.0% | 56.0% |
| 2.0R  | 33.3% | 36.7% | 40.0% | 46.7% |

The 0.75R row loses 5.8 points to the same friction the 2.0R row loses 3.4 to.
That is the single most important fact for a frequent-trading design, and it
points the opposite way from intuition: **when you trade more, widen the target,
don't tighten it.**

## 2. What a round turn actually costs

At the repo's modelled costs — `$1.24`/contract/side and 1 tick of slippage per
fill:

| | Tick value | Round turn | = ticks | Stop needed for f ≤ 0.10 | Risk/contract |
|---|---|---|---|---|---|
| MES | $1.25 | $4.98 | 3.98 | **40 ticks** (10.0 pts) | $50 |
| MNQ | $0.50 | $3.48 | 6.96 | **70 ticks** (17.5 pts) | $35 |
| M2K | $0.50 | $3.48 | 6.96 | **70 ticks** (7.0 pts) | $35 |
| MGC | $1.00 | $4.48 | 4.48 | **45 ticks** (4.5 pts) | $45 |
| MCL | $1.00 | $4.48 | 4.48 | **45 ticks** (0.45 pts) | $45 |
| MYM | $0.50 | $3.48 | 6.96 | **70 ticks** (70 pts) | $35 |

Note the cheap-tick contracts (MNQ, M2K, MYM) are *worse*, not better: the fixed
$2.48 commission is a larger multiple of a $0.50 tick.

### The trap this rules out

The instinctive way to trade more often is to shrink the stop — tighter stop,
faster resolution, more setups per day. The arithmetic says no:

```
10-tick MES stop  ->  f = 0.40R  ->  a 1R target needs a 70% win rate
```

That is not a scalping strategy, it is a donation with extra steps. **Halving
the stop doubles friction as a share of risk, and does nothing for the edge.**
So V8 gets its frequency from *more setups per day*, and enforces a stop floor
(`stop.min_stop_ticks: 40`) so a setup too tight to pay for itself is rejected
rather than traded.

## 3. What changed from V7.1, and why

| | V7.1 | V8 | Reason |
|---|---|---|---|
| Timeframe | 5m | 1m | resolve the break sooner |
| Opening range | 30 min | **5 min** | range set at 09:35, not 10:00 |
| Trades/day | 1 per instrument | **up to 3** | the actual source of frequency |
| Target | 0.75R | **1.0R** | friction-tolerance (§1) |
| Min stop | 4 ticks | **40 ticks** | cost floor (§2) |
| Risk/trade | 0.25% | **0.15%** | more losers per day (§4) |
| Session end | 15:55 | 15:40 | a 1R target needs room to travel |
| Momentum gate | 3 horizons, `all` | 3 horizons, `majority` | a stricter gate on a higher-frequency system is self-defeating |
| **Max concurrent** | **3** | **3 — unchanged** | see below |

### Concurrency stays at 3

Frequency and concurrency look like the same dial and are not. Frequency is
*sequential* risk: take a loss, then take another one later. Concurrency is
*correlated* risk: six index futures fall together, so six positions are closer
to one 6× position than to six independent bets. V7.1 measured 6 concurrent at
0.5% killing the account in four days. Nothing about trading more often makes
that safer, so that cap does not move.

### Risk per trade had to fall

The daily loss limit is a fixed $1,000 wall. More trades per day means more
losers per day means the wall arrives sooner:

```
0.25% = $125/trade  ->   8.0 consecutive losers reach the limit
0.15% =  $75/trade  ->  13.3 consecutive losers reach the limit
```

At ~10 trades/day and a ~50% win rate you expect ~5 losers a day. At $125 that
is $625 — most of the limit, on a *normal* day. At $75 it is $375. Keep the
"losers to daily limit" ratio above ~4; the dashboard reports it directly. Below
that the daily limit has stopped being a circuit breaker and become a routine
stop-out, which is how a trailing-drawdown termination starts.

## 4. A bug found in V7.1 while writing this

`config.yaml` sets a take-profit of **0.75R** and a stepped stop that triggers at
**1.0R**:

```yaml
take_profit: {mode: "fixed_r", r_multiple: 0.75}
stop_steps:  {mode: "r", steps: [{trigger: 1.0, lock: 0.0}]}
```

Open profit can never reach 1.0R, because the target fills at 0.75R first. **The
breakeven step can never change an outcome.** The README's headline description —
"Move the stop to breakeven once the trade is 1R in profit" — describes behaviour
that does not occur in any V7.1 backtest.

This does not invalidate V7.1's measured results: they were produced by a system
that effectively had no breakeven step, and they are what they are. It does mean
"breakeven at 1R" should be deleted from the description, and that **a breakeven
step has never actually been tested** on this strategy. V8 sets the trigger at
0.6R against a 1.0R target so it can fire — untested, and first in line for a
sweep.

*(Confirm against `src/backtest/engine.py`, which is not in this repository —
this is read off the config, not off a run.)*

## 5. What would falsify this before it costs anything

Run these in order; stop at the first failure.

1. **Get 1m data.** `yfinance` caps 1m history at ~7 days — useless. This needs
   `data/fetch_databento.py` or `fetch_polygon.py`. Without multi-year 1m data,
   stop here; V8 cannot be evaluated at all.
2. **Sweep the target: 0.75 / 1.0 / 1.5 / 2.0R.** If 0.75R still wins on V8's own
   data, §1 is being overwhelmed by something real about how these breakouts
   behave, and the frequency idea is probably dead on micros at $1.24/side.
3. **Sweep the stop floor: 20 / 30 / 40 / 60 ticks.** The prediction is that net
   P&L falls monotonically as the floor drops, because friction rises. If it
   doesn't, the cost model is wrong — check it before believing the result.
4. **Sweep trades/day: 1 / 2 / 3 / unlimited.** Watch net P&L *and* the worst
   daily drawdown. More trades that add P&L but push worst-day losses toward
   $1,000 is a losing trade with the prop firm even when it looks profitable.
5. **Compare against V7.1 on the same window**, on net P&L per unit of worst-case
   drawdown — not on total P&L. A system that makes more money while sitting
   closer to its trailing floor is a worse system.
6. **Forward-test on the demo account** for at least a month, and compare the
   *realised* friction to the modelled $4.98. If real slippage is 2 ticks rather
   than 1, `f` goes from 0.10R to 0.15R and every breakeven number above moves
   against you by ~2.5 points.

## 6. The honest summary

V7.1 is not proven — 60 days, one regime, selected after searching the same
window. V8 is **less** proven than that: it has never been run. Its only claim is
that it is the version of "trade more often" that the cost arithmetic does not
immediately rule out.

More trades is not more edge. It is the same edge, sampled more often, with the
tax paid more often. That is worth doing only when the edge per trade clears the
tax by a margin big enough to survive fills being worse than modelled — and the
first number to check is never P&L, it is `friction in R`.
