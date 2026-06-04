"""SFT Qwen-2.5-0.5B-Instruct on OpenRA NL→intent JSON pairs via QLoRA.

Works on:
- Colab T4 (16GB)            — full speed
- Local 4GB Win + py311 venv — set --batch 1 --grad-acc 16

Inputs:
    data/sft_train.jsonl
    data/sft_val.jsonl

Outputs:
    outputs/qwen05b-lora/   — LoRA adapter
    outputs/qwen05b-lora/training_log.json

Run:
    python scripts/train_qwen05b.py
    python scripts/train_qwen05b.py --epochs 3 --batch 2 --use-unsloth
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit",
                    help="HF model id. Unsloth pre-quantized = faster download.")
    ap.add_argument("--train", default="data/sft_train.jsonl")
    ap.add_argument("--val", default="data/sft_val.jsonl")
    ap.add_argument("--out", default="outputs/qwen05b-lora")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--grad-acc", type=int, default=4)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--max-seq", type=int, default=1024)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    ap.add_argument("--use-unsloth", action="store_true", default=True,
                    help="Use Unsloth (2x faster, 40% less VRAM)")
    ap.add_argument("--no-unsloth", dest="use_unsloth", action="store_false")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.mkdir(parents=True, exist_ok=True)

    if args.use_unsloth:
        try:
            from unsloth import FastLanguageModel
        except ImportError:
            print("[ERROR] unsloth not installed. "
                  "pip install unsloth  (or pass --no-unsloth)", file=sys.stderr)
            return 2
        print(f"[load] {args.model} via Unsloth, 4-bit")
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=args.model,
            max_seq_length=args.max_seq,
            dtype=None,
            load_in_4bit=True,
        )
        model = FastLanguageModel.get_peft_model(
            model,
            r=args.lora_r,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            lora_alpha=args.lora_alpha,
            lora_dropout=0,
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=42,
        )
    else:
        # Fallback: transformers + peft + bnb
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        print(f"[load] {args.model} via transformers + bnb, 4-bit")
        bnb_cfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        tokenizer = AutoTokenizer.from_pretrained(args.model)
        model = AutoModelForCausalLM.from_pretrained(
            args.model, quantization_config=bnb_cfg, device_map="auto",
        )
        model = prepare_model_for_kbit_training(model)
        lora_cfg = LoraConfig(
            r=args.lora_r, lora_alpha=args.lora_alpha,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            lora_dropout=0, bias="none", task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora_cfg)

    # ----- Data -----
    from datasets import load_dataset
    print(f"[data] train={args.train}  val={args.val}")
    ds = load_dataset(
        "json",
        data_files={"train": args.train, "val": args.val},
    )

    def fmt(ex):
        text = tokenizer.apply_chat_template(
            ex["messages"], tokenize=False, add_generation_prompt=False,
        )
        return {"text": text}

    ds = ds.map(fmt, remove_columns=ds["train"].column_names)
    print(f"[data] train rows: {len(ds['train'])}  val rows: {len(ds['val'])}")
    print(f"[data] sample text (first 300 chars):")
    print(ds["train"][0]["text"][:300])
    print("...")

    # ----- Trainer -----
    from trl import SFTTrainer, SFTConfig
    cfg = SFTConfig(
        output_dir=str(out_path),
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_acc,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch",
        max_length=args.max_seq,
        report_to="none",
        bf16=True,
        optim="adamw_8bit",
        seed=42,
    )
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=ds["train"],
        eval_dataset=ds["val"],
        args=cfg,
        dataset_text_field="text",
    )

    print("[train] starting...")
    t0 = time.time()
    train_result = trainer.train()
    dt = time.time() - t0

    print("[save] LoRA adapter ->", out_path)
    trainer.save_model(str(out_path))
    tokenizer.save_pretrained(str(out_path))

    log_path = out_path / "training_log.json"
    log_path.write_text(json.dumps({
        "model": args.model,
        "epochs": args.epochs,
        "batch": args.batch,
        "grad_acc": args.grad_acc,
        "lr": args.lr,
        "lora_r": args.lora_r,
        "train_size": len(ds["train"]),
        "val_size": len(ds["val"]),
        "wallclock_s": round(dt, 1),
        "final_loss": float(train_result.training_loss),
    }, indent=2), encoding="utf-8")
    print(f"[done] {dt:.1f}s, loss={train_result.training_loss:.4f}")
    print(f"        adapter at {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
