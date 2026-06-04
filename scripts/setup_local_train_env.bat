@echo off
REM Set up a Python 3.11 venv for local training on 4GB GPU.
REM Alternative to Colab — use only if you insist on training locally.
REM
REM Prereq:
REM   - Python 3.11 installed (NOT 3.13 — bnb/unsloth lack 3.13 wheels)
REM     Download: https://www.python.org/downloads/release/python-3119/
REM   - NVIDIA driver with CUDA 12.1+
REM
REM Usage:
REM   scripts\setup_local_train_env.bat
REM   .venv-train\Scripts\activate
REM   python scripts\train_qwen05b.py --batch 1 --grad-acc 16

setlocal

echo === checking python 3.11 ===
py -3.11 --version 2>nul
if errorlevel 1 (
    echo [ERROR] python 3.11 not found.
    echo Install from https://www.python.org/downloads/release/python-3119/ first.
    exit /b 2
)

if not exist .venv-train (
    echo === creating .venv-train ===
    py -3.11 -m venv .venv-train
) else (
    echo === .venv-train already exists, reusing ===
)

call .venv-train\Scripts\activate.bat
python -m pip install --upgrade pip wheel

echo === installing torch CUDA 12.1 ===
pip install torch --index-url https://download.pytorch.org/whl/cu121

echo === installing unsloth + training stack ===
pip install "unsloth[cu121-torch251] @ git+https://github.com/unslothai/unsloth.git"
pip install --no-deps "trl<0.20" peft accelerate bitsandbytes datasets

echo === verifying ===
python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available()); print('gpu', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
python -c "import unsloth; print('unsloth', unsloth.__version__)"

echo ============================================================
echo Local training env ready in .venv-train\
echo To train:
echo   .venv-train\Scripts\activate
echo   python scripts\train_qwen05b.py --batch 1 --grad-acc 16
echo ============================================================
endlocal
