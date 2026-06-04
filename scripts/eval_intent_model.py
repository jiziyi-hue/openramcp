"""Evaluate the fine-tuned Qwen LoRA on the held-out val set.

Metrics:
- JSON parse rate
- intent_type accuracy (top-level "intent" or "_tool" field)
- force.kind accuracy (for dispatch_intent intents)
- target match accuracy (for attack/feint/etc)
- full exact match

Usage:
    python scripts/eval_intent_model.py
    python scripts/eval_intent_model.py --adapter outputs/qwen05b-lora --val data/sft_val.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def parse_assistant(text: str) -> dict | None:
    """Extract JSON dict from assistant output (tolerate code fences)."""
    text = text.strip()
    if text.startswith("```"):
        # strip fence
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # try direct
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # try to find {...} substring
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def top_kind(obj: dict | None) -> str:
    if not isinstance(obj, dict):
        return "_unparseable"
    return obj.get("intent") or obj.get("_tool") or "_unknown"


def force_kind(obj: dict | None) -> str | None:
    if not isinstance(obj, dict):
        return None
    f = obj.get("force") or obj.get("feint_force") or obj.get("left")
    if isinstance(f, dict):
        return f.get("kind")
    return None


def target_str(obj: dict | None) -> str | None:
    if not isinstance(obj, dict):
        return None
    t = obj.get("target")
    if isinstance(t, dict) and t.get("kind") == "named":
        return t.get("name")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit")
    ap.add_argument("--adapter", default="outputs/qwen05b-lora")
    ap.add_argument("--val", default="data/sft_val.jsonl")
    ap.add_argument("--max-new", type=int, default=256)
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="outputs/qwen05b-lora/eval_report.json")
    ap.add_argument("--use-unsloth", action="store_true", default=True)
    ap.add_argument("--no-unsloth", dest="use_unsloth", action="store_false")
    ap.add_argument("--cpu", action="store_true",
                    help="Load fp32 base on CPU + adapter (no GPU/bnb/unsloth)")
    ap.add_argument("--cpu-base", default="Qwen/Qwen2.5-0.5B-Instruct",
                    help="Non-quantized base for CPU eval")
    args = ap.parse_args()

    # Load
    if args.cpu:
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        from peft import PeftModel
        print(f"[load] CPU fp32: base={args.cpu_base} + adapter={args.adapter}")
        tokenizer = AutoTokenizer.from_pretrained(args.adapter)
        base = AutoModelForCausalLM.from_pretrained(
            args.cpu_base, torch_dtype=torch.float32,
        )
        model = PeftModel.from_pretrained(base, args.adapter)
        model.eval()
    elif args.use_unsloth:
        from unsloth import FastLanguageModel
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=args.adapter,
            max_seq_length=1024,
            load_in_4bit=True,
        )
        FastLanguageModel.for_inference(model)
    else:
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        from peft import PeftModel
        tokenizer = AutoTokenizer.from_pretrained(args.adapter)
        base = AutoModelForCausalLM.from_pretrained(
            args.base, device_map="auto", load_in_4bit=True,
        )
        model = PeftModel.from_pretrained(base, args.adapter)
        model.eval()

    # Load val
    val = []
    with open(args.val, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                val.append(json.loads(line))
    if args.limit:
        val = val[: args.limit]
    print(f"[eval] {len(val)} examples")

    # Run
    parse_ok = 0
    intent_acc = 0
    force_acc = 0
    target_acc = 0
    exact = 0
    intent_acc_by_type: Counter = Counter()
    intent_total_by_type: Counter = Counter()
    errors: list[dict] = []

    import torch
    for i, ex in enumerate(val):
        msgs = ex["messages"]
        # last message is assistant ground truth; cut for inference
        gt_str = msgs[-1]["content"]
        prompt_msgs = msgs[:-1]
        prompt = tokenizer.apply_chat_template(
            prompt_msgs, tokenize=False, add_generation_prompt=True,
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=args.max_new,
                do_sample=args.temperature > 0,
                temperature=args.temperature,
                pad_token_id=tokenizer.eos_token_id,
            )
        gen = tokenizer.decode(
            out[0][inputs.input_ids.shape[1]:], skip_special_tokens=True,
        )
        pred = parse_assistant(gen)
        gt = json.loads(gt_str)
        gt_kind = top_kind(gt)
        intent_total_by_type[gt_kind] += 1

        if pred is not None:
            parse_ok += 1
            if top_kind(pred) == gt_kind:
                intent_acc += 1
                intent_acc_by_type[gt_kind] += 1
            if force_kind(pred) and force_kind(pred) == force_kind(gt):
                force_acc += 1
            if target_str(pred) and target_str(pred) == target_str(gt):
                target_acc += 1
            if json.dumps(pred, sort_keys=True, ensure_ascii=False) == \
               json.dumps(gt, sort_keys=True, ensure_ascii=False):
                exact += 1
            else:
                if len(errors) < 20:
                    errors.append({
                        "nl": prompt_msgs[-1]["content"],
                        "gt": gt_str,
                        "pred": gen.strip()[:400],
                    })
        else:
            if len(errors) < 20:
                errors.append({
                    "nl": prompt_msgs[-1]["content"],
                    "gt": gt_str,
                    "pred_raw": gen.strip()[:400],
                    "parse_failed": True,
                })

        if (i + 1) % 20 == 0:
            print(f"  [{i + 1}/{len(val)}] parse={parse_ok} "
                  f"intent={intent_acc} exact={exact}")

    n = len(val)
    report = {
        "n": n,
        "parse_rate": parse_ok / n,
        "intent_accuracy": intent_acc / n,
        "force_kind_accuracy": force_acc / n,
        "target_accuracy": target_acc / n,
        "exact_match": exact / n,
        "intent_accuracy_by_type": {
            k: (intent_acc_by_type[k] / v)
            for k, v in intent_total_by_type.items()
        },
        "intent_total_by_type": dict(intent_total_by_type),
        "errors_sample": errors,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    print("=" * 50)
    print(f"parse_rate:        {report['parse_rate']:.2%}")
    print(f"intent_accuracy:   {report['intent_accuracy']:.2%}")
    print(f"force_kind_acc:    {report['force_kind_accuracy']:.2%}")
    print(f"target_accuracy:   {report['target_accuracy']:.2%}")
    print(f"exact_match:       {report['exact_match']:.2%}")
    print(f"report -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
