# Qwen-2.5-0.5B OpenRA Intent Model — Training Results

**Date**: 2026-06-04
**Base**: `unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit` (4-bit)
**Method**: QLoRA SFT, r=16, alpha=32, all 7 attn+mlp modules
**Hardware**: Colab T4 (16GB), fp16
**Data**: 1828 train / 203 val ChatML pairs (51 real + 1980 synth)
**Config**: 3 epochs, lr=2e-4, cosine, batch 8 × grad-acc 2, max_seq 1024
**Final training loss**: **0.2972**
**Wallclock**: ~6 min train

## Inference sanity (greedy, held-out style prompts)

| Player NL | Model output | Verdict |
|---|---|---|
| 北队正面冲他重工 | `{"intent":"attack","force":{"kind":"group","name":"north"},"target":{"kind":"named","name":"enemy_fact"},"approach":"frontal"}` | ✅ correct |
| 残血的全撤回去 | `{"intent":"retreat","force":{"kind":"filter","owner":"self","hp_below":0.5},"to":{"kind":"named","name":"self_base"}}` | ✅ correct (low-hp filter) |
| 南北夹击他老家 | `{"intent":"pincer","left":{"kind":"group","name":"north"},"right":{"kind":"group","name":"south"},"target":{"kind":"named","name":"enemy_base"},"rendezvous_dist":8}` | ✅ correct |
| 切 alert | `{...,"alert_state":"alert"}` | ✅ correct (set_alert_state) |
| 总动员 | `{"_tool":"set_doctrine","alert_state":"combat","objective":"control_map_center"}` | ⚠️ partial — doctrine+combat right, objective should be destroy_enemy |
| 派几个去骚扰他经济 | `{"intent":"harass","force":{"kind":"filter","harass_capable":true},"region":{"kind":"around","center":"enemy_base","radius":6},"cycle":false,"withdraw_hp_threshold":0.5}` | ✅ correct |
| 看看场上 | `{"intent":"report","what":"battlefield"}` | ✅ correct |

**6/7 fully correct, 1 partial.** A 0.5B model reproduces the intent DSL after ~6 min training —
strong support for the paradigm claim that engine-side FSM offload shrinks the LLM-side
burden into small-model range.

## Quantitative eval (full 203 val set, CPU fp32, greedy)

| Metric | Value |
|---|---|
| **parse_rate** | **100.00%** — every output was valid JSON, zero malformed |
| **intent_accuracy** | **71.92%** — correct intent/_tool field |
| **force_kind_accuracy** | 76.35% (of all 203; non-force intents can't contribute) |
| **target_accuracy** | 32.02% (of all 203; only attack/feint/pincer carry a named target) |
| **exact_match** | **43.35%** — entire JSON byte-identical to ground truth |

### Per-intent accuracy (honest breakdown)

Strong (high-freq, structurally clear):
- `attack` **98%** (n=81, the dominant use case) · `report`/`scout`/`patrol`/`escort` **100%**
  · `set_stance` 77% · `pincer` 71% · `feint` 75% · `regroup` 67%

Weak (the `_tool` high-level calls + low-freq + semantically subtle):
- `set_objective` 11% (n=9) · `set_alert_state` 20% · `set_doctrine` 25% ·
  `cancel_assaults` 0% (n=2) · `retreat` 25% (confused with regroup) ·
  `defend` 42% · `contain` 25%

### Root cause of the weak classes

The `_tool`-shaped high-level calls (set_objective / set_alert_state / set_doctrine /
cancel_assaults) are **underrepresented** (5–9 synth examples each) AND structurally
differ from the dominant `{"intent":...}` shape, so the 0.5B model defaults to emitting
an `intent` even when a `_tool` is required. Examples:
- "小心点" → predicted `{"intent":"alert","level":"watch"}` (invented intent) instead of
  `{"_tool":"set_alert_state","level":"alert"}`
- "全力龟缩撑到timeout" → `set_stance Defend` instead of
  `{"_tool":"set_objective","name":"survive_until_tick"}`
- "所有人回家" → `regroup` instead of `retreat` (genuinely close semantics)

**Takeaway**: 100% valid JSON + 98% on the dominant `attack` path strongly supports the
small-model-tractability claim. The fix for the weak classes is data, not capacity:
rebalance synthesis toward `_tool` calls (raise from ~5 to ~30 templates each) and add
retreat-vs-regroup contrast pairs, then retrain.

## Files

- `adapter_model.safetensors` — LoRA weights (~35MB, gitignored)
- `adapter_config.json` — PEFT config (committed)
- `tokenizer*` / `chat_template.jinja` — tokenizer (gitignored, re-downloadable)
- `TRAINING_RESULTS.md` — this file

## Next steps

1. **Quantitative eval**: `python scripts/eval_intent_model.py` on the 203 val set
   → parse_rate / intent_accuracy / exact_match numbers for the paper.
2. **Fix the 总动员 → objective miss**: add more `destroy_enemy` synthesis examples
   (currently underrepresented vs control_map_center) and retrain.
3. **MCP integration**: wrap adapter as a local inference server, A/B vs DeepSeek
   on the 21 T-scenarios → token / turn comparison row.
4. **Paper 2 framing**: "intent-translation is a small-model-tractable task under the
   delegation paradigm" — this run is the seed result.
