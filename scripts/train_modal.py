"""Train the Qwen-0.5B LoRA on a cloud GPU via Modal — pure CLI, no browser.

One-time setup (you, ~3 min):
    pip install modal
    modal setup                # opens a browser ONCE to auth, then never again

Then I drive it from the terminal:
    modal run scripts/train_modal.py --version v6
    modal volume get openra-out v6 outputs/qwen05b-lora-v6   # pull the adapter

Data is pulled from the public GitHub repo, trained on a T4, saved to a
Modal Volume. ~5 min compute, a few cents of free-tier credit.
"""
import modal

app = modal.App("openra-sft")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch", "transformers", "peft", "accelerate",
        "bitsandbytes", "datasets",
    )
)

vol = modal.Volume.from_name("openra-out", create_if_missing=True)

BASE = "https://raw.githubusercontent.com/jiziyi-hue/openramcp/main/data"
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"


@app.function(gpu="T4", image=image, volumes={"/out": vol}, timeout=2400)
def train(version: str = "v6", epochs: int = 3):
    import subprocess
    import json
    import time
    import torch
    from transformers import (AutoTokenizer, AutoModelForCausalLM,
                              BitsAndBytesConfig, Trainer, TrainingArguments,
                              DataCollatorForSeq2Seq)
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from torch.utils.data import Dataset

    subprocess.run(["wget", "-qO", "train.jsonl", f"{BASE}/sft_train.jsonl"], check=True)
    subprocess.run(["wget", "-qO", "val.jsonl", f"{BASE}/sft_val.jsonl"], check=True)
    print("data:", subprocess.run(["wc", "-l", "train.jsonl", "val.jsonl"],
                                  capture_output=True, text=True).stdout)

    tok = AutoTokenizer.from_pretrained(MODEL)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def load(path, max_len=512):
        rows = []
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            msgs = json.loads(line)["messages"]
            full = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
            prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
            fids = tok(full, truncation=True, max_length=max_len).input_ids
            pids = tok(prompt, truncation=True, max_length=max_len).input_ids
            labels = list(fids)
            for i in range(min(len(pids), len(labels))):
                labels[i] = -100
            rows.append({"input_ids": fids, "labels": labels,
                         "attention_mask": [1] * len(fids)})
        return rows

    class Rows(Dataset):
        def __init__(s, r): s.r = r
        def __len__(s): return len(s.r)
        def __getitem__(s, i): return s.r[i]

    train_rows, val_rows = Rows(load("train.jsonl")), Rows(load("val.jsonl"))
    print(f"train {len(train_rows)} val {len(val_rows)}")

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.float16,
                             bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(MODEL, quantization_config=bnb,
                                                 device_map="auto")
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.0, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"]))
    print("trainable:", sum(p.numel() for p in model.parameters() if p.requires_grad))

    out = f"/out/{version}"
    targs = TrainingArguments(
        output_dir=out, per_device_train_batch_size=8,
        gradient_accumulation_steps=2, num_train_epochs=epochs,
        learning_rate=2e-4, warmup_ratio=0.05, lr_scheduler_type="cosine",
        logging_steps=20, save_strategy="no", eval_strategy="epoch",
        fp16=True, optim="adamw_8bit", report_to="none", seed=42)
    trainer = Trainer(model=model, args=targs, train_dataset=train_rows,
                      eval_dataset=val_rows,
                      data_collator=DataCollatorForSeq2Seq(tok, padding=True))
    t0 = time.time()
    res = trainer.train()
    print(f"=== trained {time.time()-t0:.0f}s loss={res.training_loss:.4f} ===")

    model.save_pretrained(out)
    tok.save_pretrained(out)
    vol.commit()
    print(f"saved adapter to volume openra-out:/{version}")
    return {"loss": float(res.training_loss), "version": version}


@app.local_entrypoint()
def main(version: str = "v6", epochs: int = 3):
    r = train.remote(version=version, epochs=epochs)
    print("RESULT:", r)
    print(f"download: modal volume get openra-out {version} "
          f"outputs/qwen05b-lora-{version}")
