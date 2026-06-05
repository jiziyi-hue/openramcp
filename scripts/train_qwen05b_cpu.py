"""Train Qwen-2.5-0.5B LoRA on CPU (fp32) — no GPU/bnb/unsloth/datasets/trl.

Uses transformers Trainer + peft only. Masks the prompt so loss is on the
assistant JSON completion. Slower than Colab T4 but fully local + autonomous.

Run:
    python scripts/train_qwen05b_cpu.py --out outputs/qwen05b-lora-v4
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

BASE = "Qwen/Qwen2.5-0.5B-Instruct"


def load_rows(path: str, tok, max_len: int) -> list[dict]:
    rows: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        msgs = json.loads(line)["messages"]
        full = tok.apply_chat_template(msgs, tokenize=False,
                                       add_generation_prompt=False)
        prompt = tok.apply_chat_template(msgs[:-1], tokenize=False,
                                         add_generation_prompt=True)
        full_ids = tok(full, truncation=True, max_length=max_len).input_ids
        prompt_ids = tok(prompt, truncation=True, max_length=max_len).input_ids
        labels = list(full_ids)
        for i in range(min(len(prompt_ids), len(labels))):
            labels[i] = -100  # mask prompt; train only on the JSON completion
        rows.append({"input_ids": full_ids, "labels": labels,
                     "attention_mask": [1] * len(full_ids)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/sft_train.jsonl")
    ap.add_argument("--val", default="data/sft_val.jsonl")
    ap.add_argument("--out", default="outputs/qwen05b-lora-v4")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--grad-acc", type=int, default=2)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--max-len", type=int, default=512)
    args = ap.parse_args()

    import torch
    from transformers import (AutoTokenizer, AutoModelForCausalLM, Trainer,
                              TrainingArguments, DataCollatorForSeq2Seq)
    from peft import LoraConfig, get_peft_model

    print(f"[load] tokenizer + base {BASE} (fp32, CPU)", flush=True)
    tok = AutoTokenizer.from_pretrained(BASE)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    from torch.utils.data import Dataset

    class Rows(Dataset):
        def __init__(self, rows):
            self.rows = rows

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, i):
            return self.rows[i]

    train_rows = Rows(load_rows(args.train, tok, args.max_len))
    val_rows = Rows(load_rows(args.val, tok, args.max_len))
    print(f"[data] train={len(train_rows)} val={len(val_rows)}", flush=True)

    model = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.float32)
    model = get_peft_model(model, LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.0, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    ))
    n_train = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"[lora] trainable params: {n_train:,}", flush=True)

    collator = DataCollatorForSeq2Seq(tok, padding=True, return_tensors="pt")
    targs = TrainingArguments(
        output_dir=args.out,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_acc,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        logging_steps=20,
        save_strategy="epoch",
        eval_strategy="epoch",
        report_to="none",
        use_cpu=True,
        dataloader_num_workers=0,
        seed=42,
    )
    trainer = Trainer(
        model=model, args=targs,
        train_dataset=train_rows, eval_dataset=val_rows,
        data_collator=collator,
    )
    print("[train] starting (CPU, this is slow — ~30-60 min)...", flush=True)
    t0 = time.time()
    result = trainer.train()
    dt = time.time() - t0

    Path(args.out).mkdir(parents=True, exist_ok=True)
    trainer.save_model(args.out)
    tok.save_pretrained(args.out)
    (Path(args.out) / "training_log.json").write_text(json.dumps({
        "base": BASE, "epochs": args.epochs, "batch": args.batch,
        "train_size": len(train_rows), "val_size": len(val_rows),
        "wallclock_s": round(dt, 1),
        "final_loss": float(result.training_loss),
        "device": "cpu",
    }, indent=2), encoding="utf-8")
    print(f"[done] {dt:.0f}s, loss={result.training_loss:.4f} -> {args.out}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
