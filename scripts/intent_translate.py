"""Local NL -> intent JSON translator using the fine-tuned v3 Qwen-0.5B adapter.

Loads the base model (fp32, CPU) + LoRA adapter once, then translates Chinese
player utterances into openra_mcp tool-call JSON. This is the bridge between the
trained adapter and actual use in the MCP server.

Usage:
    # translate sentences passed as args
    python scripts/intent_translate.py "北队推他老家" "残血的撤" "切戒备"

    # translate a file of sentences (one per line)
    python scripts/intent_translate.py --file sentences.txt

    # interactive
    python scripts/intent_translate.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

ADAPTER = "outputs/qwen05b-lora-v3"
BASE = "Qwen/Qwen2.5-0.5B-Instruct"


def parse_json(text: str) -> dict | None:
    """Extract a JSON object from model output (tolerate code fences/extra text)."""
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:]).rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    a, b = text.find("{"), text.rfind("}")
    if 0 <= a < b:
        try:
            return json.loads(text[a:b + 1])
        except json.JSONDecodeError:
            return None
    return None


class Translator:
    def __init__(self, adapter: str = ADAPTER, base: str = BASE):
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        from peft import PeftModel

        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(adapter)
        with open(Path(adapter).parent.parent / "data" / "sft_train.jsonl"
                  if (Path("data") / "sft_train.jsonl").exists()
                  else "data/sft_train.jsonl", encoding="utf-8") as f:
            self.system_prompt = json.loads(f.readline())["messages"][0]["content"]
        base_model = AutoModelForCausalLM.from_pretrained(base, dtype=torch.float32)
        self.model = PeftModel.from_pretrained(base_model, adapter)
        self.model.eval()

    def translate(self, nl: str) -> dict | None:
        msgs = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": nl},
        ]
        prompt = self.tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True)
        inp = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        with self.torch.no_grad():
            out = self.model.generate(
                **inp, max_new_tokens=128, do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id)
        gen = self.tokenizer.decode(
            out[0][inp.input_ids.shape[1]:], skip_special_tokens=True)
        return parse_json(gen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sentences", nargs="*", help="NL utterances to translate")
    ap.add_argument("--file", help="file with one utterance per line")
    ap.add_argument("--adapter", default=ADAPTER)
    args = ap.parse_args()

    print(f"[load] {args.adapter} (CPU, first load ~10s)...", file=sys.stderr)
    tr = Translator(adapter=args.adapter)
    print("[ready]", file=sys.stderr)

    inputs: list[str] = list(args.sentences)
    if args.file:
        inputs += [ln.strip() for ln in Path(args.file).read_text(
            encoding="utf-8").splitlines() if ln.strip()]

    if not inputs:  # interactive
        print("输入中文指令 (空行退出):", file=sys.stderr)
        while True:
            try:
                nl = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not nl:
                break
            print(json.dumps(tr.translate(nl), ensure_ascii=False))
        return 0

    for nl in inputs:
        out = tr.translate(nl)
        print(f"{nl}\t{json.dumps(out, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
