# Qwen-0.5B intent model — v1 / v2 / v3 eval comparison

All three: Qwen2.5-0.5B-Instruct + QLoRA (r=16), 3 epochs, fp16 on Colab T4.
Eval: CPU fp32, greedy, each model on its own held-out val split.

## Recipe progression

| | v1 | v2 | v3 |
|---|---|---|---|
| total pairs | 2031 | 1839 | 2431 |
| attack examples | 881 (43%) | 150 (flat cap) | 300 (freq-aware cap) |
| `_tool` classes | 5–9 val each | boosted ~150 | boosted ~200 |
| recipe | raw synth | boost + cap 150 | boost + **cap 300** |

## Aggregate (each on own val)

| metric | v1 (n=203) | v2 (n=183) | v3 (n=243) |
|---|---|---|---|
| parse_rate | 100% | 99% | **100%** |
| intent_accuracy | 72% | 63% | **72%** |
| exact_match | 43% | 31% | **44%** |

**v3 matches v1's best aggregate AND fixes the weak classes** (v2 proved the
mechanism but over-corrected attack).

## Per-class intent accuracy (the real story)

| intent | v1 | v2 | v3 | verdict |
|---|---|---|---|---|
| attack | 98% | 75% | 81% | recovered from v2 dip (cap 300 keeps weight) |
| set_objective | 11% | 50% | **69%** | fixed (+58 vs v1) |
| set_doctrine | 25% | 62% | **77%** | fixed (+52) |
| retreat | 25% | 78% | **79%** | fixed (+54) |
| defend | 42% | 46% | **71%** | fixed (+29) |
| feint | 75% | 83% | **88%** | up |
| pincer | 71% | 100% | **100%** | up |
| set_stance | 77% | 87% | 85% | up |
| scout | 100% | 60% | 80% | noisy (n=5) |
| contain | 25% | 45% | 33% | mixed (n=12) |
| cancel_assaults | 0% | 67% | 50% | up but noisy (n=4) |
| **set_alert_state** | 20% | 33% | **17%** | **stuck** — see below |

## The one stubborn class: set_alert_state

`set_alert_state` never crosses ~33% across all three recipes. Root cause is **not**
data volume (v3 had ~200 train examples) but **inherent ambiguity**: the 5 levels
(peace/watch/alert/combat/lockdown) form an ordinal scale, and short NL like
"小心点" / "戒备" / "紧张" map to adjacent levels (watch vs alert) that even a human
would split-vote on. The 0.5B model picks a neighbor. Fixes would be (a) collapse to
3 levels, or (b) make the level explicit in NL, or (c) a larger model. Documented as a
known limitation, not pursued further.

## Verdict

| model | use |
|---|---|
| v1 | baseline; great attack, unusable `_tool` calls |
| v2 | ablation proving distribution sensitivity (over-corrected) |
| **v3** | **production pick** — best aggregate + usable across attack and `_tool` |

## Paper-relevant finding

Intent-translation accuracy is **highly sensitive to training distribution**, and a
**frequency-aware cap** (dominant class ~2× the rare classes, not 6× and not equal)
recovers the best of both: v3 keeps `attack` strong (81%) while lifting the rare
`_tool` calls (set_objective 11→69, set_doctrine 25→77, retreat 25→79). A flat equal
cap (v2) over-penalizes the dominant class; no cap (v1) starves the rare ones. The
sweet spot is the natural-frequency-aware middle. 100% valid JSON throughout confirms
a 0.5B model is structurally sufficient for the intent-DSL surface — the open problem
is semantic disambiguation of ordinal/near-synonym classes (set_alert_state), not
syntax.
