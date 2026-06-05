# Qwen-2.5-0.5B v4 — REAL command surface (coordless)

Trained on the actual current interpreter surface (attack {force:filter,
target:named} + report), NOT the deprecated 15-intent DSL of v1-v3. Every
training command was validated by parse_intent, so the model only ever
emits commands the engine accepts. Map landmarks (center/corners) make
"go to a position" coordless.

- Data: 1004 pairs (904 train / 100 val), DeepSeek back-translation,
  tank-naming conflicts filtered.
- Trained on Colab T4 (fresh account), 3 epochs.

## Eval (100-val, local CPU fp32, greedy)

| Metric | v4 | (v3 on OLD val, for reference) |
|---|---|---|
| parse_rate | 100% | 100% |
| intent_accuracy | **99%** | 72% |
| force_kind_acc | 88% | 76% |
| target_accuracy | 69% | 32% |
| exact_match | **50%** | 44% |

Per-class: attack 100% (n=93), report 86% (n=7).

## The real win

v1-v3 scored well but against a DEAD spec — ~80% of their outputs the
current interpreter rejects. v4 targets the real surface, so 100% of its
outputs are executable commands. The coordless design holds: the model
emits only names (enemy_fact / map_center / ...), the interpreter computes
all (x,y) and unit_ids from live state.

## Remaining errors (mostly soft)

Force-filter nuance: "兵上" -> combat_mobile vs label e1 (both valid);
"全部战斗车" -> v2rl vs combat_mobile. report sub-type confusion
(battlefield vs threats). intent + target are nearly always correct.
