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
