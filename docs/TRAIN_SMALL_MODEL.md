# 训练 Qwen-2.5-0.5B 专用 NL→intent 模型

> 目标: 用一个本地可跑的 0.5B 小模型替代 DeepSeek API, 把玩家中文 NL
> 翻成 openra_mcp 工具调用 JSON. 验证 paradigm 把 LLM 负担降到 0.5B
> 能扛的范围.

## 流程总览

```
decisions.jsonl (real)  ──┐
                          ├─►  build_sft_dataset.py  ─►  sft_train/val.jsonl
synthesize (DeepSeek API) ─┘                                   │
                                                               ▼
                                              ┌──── train_qwen05b.py (本地/Colab) ────┐
                                              │              │                          │
                                              ▼              ▼                          ▼
                                       Colab T4         本地 4GB GPU           outputs/qwen05b-lora/
                                       (推荐)            (要 py311 venv)        adapter (~30MB)
                                                                                       │
                                                                                       ▼
                                                                          eval_intent_model.py
                                                                                       │
                                                                                       ▼
                                                                          eval_report.json
```

## 脚本清单

| 脚本 | 作用 | 何时跑 |
|---|---|---|
| `scripts/extract_training_data.py` | 抽真实 `decisions.jsonl` 的 (NL, JSON) 对 | 任何时候 |
| `scripts/synthesize_training_data.py` | DeepSeek 反向产 198 模板 × 10 NL ≈ 2000 对 | 任何时候 (要 `DEEPSEEK_API_KEY`) |
| `scripts/build_sft_dataset.py` | 合并真+合成, dedup, train/val split, ChatML 格式化 | 上两个跑完后 |
| `scripts/train_qwen05b.py` | Unsloth + QLoRA SFT 训练 | 本地 (要 py311 venv) |
| `notebooks/train_qwen05b_colab.ipynb` | 同上的 Colab 版 | Colab T4 (推荐) |
| `scripts/eval_intent_model.py` | 跑 val 测 parse / intent / target 准确率 | 训完 |
| `scripts/setup_local_train_env.bat` | 本地 py311 venv + torch+cu121+unsloth+bnb | 想本地训才用 |

## 完整运行步骤

### 1. 准备数据 (本地, ~30 分钟)

```cmd
cd /d D:\openra_mcp

REM 抽真实数据 (~50 对)
python scripts\extract_training_data.py

REM 合成 2000 对 — 要 DeepSeek API key
set DEEPSEEK_API_KEY=sk-...
python scripts\synthesize_training_data.py

REM 合并 + 切分
python scripts\build_sft_dataset.py
REM 产出 data/sft_train.jsonl + data/sft_val.jsonl + data/sft_meta.json
```

### 2A. 训练 — Colab T4 路径 (推荐)

1. 打开 `notebooks/train_qwen05b_colab.ipynb`
2. 上传到 [colab.research.google.com](https://colab.research.google.com/)
3. Runtime → Change runtime type → **T4 GPU**
4. Runtime → Run all
5. Cell 2 弹窗上传你刚生成的 `sft_train.jsonl` + `sft_val.jsonl`
6. 等 ~20 分钟训练完
7. 最后一格自动下载 `qwen05b_openra_lora.zip` 到本地
8. 解压到 `outputs/qwen05b-lora/`

### 2B. 训练 — 本地 4GB GPU 路径 (需 py311)

```cmd
REM 一次性 setup (装 py311 venv + torch + unsloth + bnb)
scripts\setup_local_train_env.bat

REM 训练 (4GB 友好超参)
.venv-train\Scripts\activate
python scripts\train_qwen05b.py --batch 1 --grad-acc 16
```

### 3. 评估

```cmd
python scripts\eval_intent_model.py
REM 看 outputs/qwen05b-lora/eval_report.json
```

期待数据 (拍脑袋):
- parse_rate ≥ 95% (constrained domain, 0.5B 应该轻松产合法 JSON)
- intent_accuracy ≥ 70% (核心 intent 应该学得动)
- exact_match ≥ 40% (整 JSON 全对率, 0.5B 比较挑战)

如果 parse_rate < 80% → 加数据 / 加 epoch / 上 constrained decoding
如果 intent_accuracy < 50% → 模型容量不够, 升 Qwen-2.5-1.5B 重训

### 4. 集成回 MCP server

待 paper 2 决定. 简单 wrapper 就行 — 改 `mcp_server` 调用 LLM 的地方,
加 `--use-local-model` 开关, 加载 `outputs/qwen05b-lora/` 替代 DeepSeek
API.

## 4GB VRAM 显存账 (本地训练时)

| 项 | 占用 |
|---|---|
| Qwen-0.5B 基模 4-bit | ~0.4 GB |
| LoRA adapter (r=16) | ~20 MB |
| 梯度 + AdamW 优化器状态 | ~80 MB |
| Activations (batch=1, seq=1024) | ~1.5 GB |
| KV cache / 临时 buffer | ~0.5 GB |
| **训练总计** | **~2.5 GB** ✅ |
| **推理 (fp16)** | **~0.8 GB** ✅ |

0.5B + 4GB 实际很宽松, 你可以试 batch=2 也不一定爆.

## 训练成本

| 路径 | 时间 | 钱 |
|---|---|---|
| Colab T4 | ~20 分钟 | 免费 |
| 本地 4GB (RTX 3050 类) | ~40-60 分钟 | 免费 |
| RunPod A100 | ~5 分钟 | ~$0.10 |
| Modal A10G | ~10 分钟 | ~$0.05 |

合成数据成本: ~$0.10 (DeepSeek v4-flash 198 calls).
