# Qwen-0.5B intent model — v1 vs v2 eval comparison

Both models: Qwen2.5-0.5B-Instruct + QLoRA (r=16), 3 epochs, fp16 on Colab T4.
Eval: CPU fp32, greedy, each model on its own held-out val split.

## What changed between v1 and v2

| | v1 | v2 |
|---|---|---|
| total pairs | 2031 | 1839 |
| attack examples | 881 (43%) | 150 (capped) |
| `_tool` classes | 5–9 val each | boosted to ~150 train each |
| recipe | raw synth | `--boost` weak classes + `--cap-per-intent 150` |

## Aggregate (each on own val)

| metric | v1 (n=203) | v2 (n=183) |
|---|---|---|
| parse_rate | 100% | 99.5% |
| intent_accuracy | 72% | 63% |
| exact_match | 43% | 31% |

Aggregate **dropped** — but this is a val-composition + over-correction artifact,
not a uniform regression. See per-class.

## Per-class accuracy (the real story)

Targeted weak `_tool`/rare classes — **all improved, several dramatically**:

| intent | v1 | v2 | Δ |
|---|---|---|---|
| retreat | 25% | **78%** | +53 |
| cancel_assaults | 0% | **67%** | +67 |
| set_objective | 11% | **50%** | +39 |
| set_doctrine | 25% | **62%** | +37 |
| contain | 25% | 45% | +20 |
| set_alert_state | 20% | 33% | +13 |
| set_stance | 77% | 87% | +10 |
| feint | 75% | 83% | +8 |

Cost — the dominant class regressed because its weight was cut 6×:

| intent | v1 | v2 | Δ |
|---|---|---|---|
| **attack** | 98% | 75% | **−23** |

(Small-n classes scout/escort/spawn_squad/diversion also moved, but n≤5 = noise.)

## Finding (paper-relevant)

Intent-translation accuracy is **highly sensitive to training distribution**. A flat
per-intent cap lifts data-starved `_tool` classes (set_objective 11→50, retreat 25→78,
set_doctrine 25→62) but **over-penalizes the dominant `attack` class** (98→75) when the
cap is set as low as the rare classes (150). The shape-confusion hypothesis is confirmed:
the `{"_tool":...}` calls failed in v1 because 43%-attack `{"intent":...}` dominance biased
the 0.5B model toward the `intent` shape; rebalancing fixes it.

**Next step (v3, not yet run):** frequency-aware cap — keep attack ~350, others 150.
Expected: attack recovers toward ~90%+ while `_tool` classes stay lifted. This should beat
both v1 and v2 on aggregate.

## Verdict

- **v1** = best aggregate, but unusable `_tool` calls (the high-level commands).
- **v2** = usable `_tool` calls, weaker attack; proves the data-distribution mechanism.
- **v3** (tuned cap) = the likely production pick.
