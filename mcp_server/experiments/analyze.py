"""Aggregate per-session JSONL + summary into paper-grade figures.

Reads:
    logs/<session_id>/decisions.jsonl
    logs/<session_id>/session_summary.json
    logs/<session_id>/session_meta.json (for condition tagging)

Emits:
    experiments/results/runs.csv
    experiments/results/figures/fig1_amplification.svg
    experiments/results/figures/fig2_apm_timeline.svg
    experiments/results/figures/fig3_llm_latency.svg
    experiments/results/figures/fig4_concurrent_fronts.svg
    experiments/results/figures/fig5_template_usage.svg

Run:   python -m mcp_server.experiments.analyze --logs logs/ --out experiments/results/
"""

from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path


def load_sessions(logs_root: Path) -> list[dict]:
    """Return a list of session dicts (one per subfolder under logs_root)."""
    rows: list[dict] = []
    if not logs_root.exists():
        return rows
    for sub in sorted(logs_root.iterdir()):
        if not sub.is_dir():
            continue
        summary_path = sub / "session_summary.json"
        meta_path = sub / "session_meta.json"
        if not summary_path.exists():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        row = dict(summary)
        row["condition"] = meta.get("condition", "unknown")
        row["scenario_id"] = meta.get("scenario_id", "unknown")
        rows.append(row)
    return rows


def write_runs_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    # Stable column order
    cols = [
        "session_id", "condition", "scenario_id", "duration_minutes",
        "nl_commands", "atomic_orders", "mean_amplification_ratio",
        "apm", "player_decisions_per_min",
        "total_input_tokens", "total_output_tokens", "estimated_llm_cost_usd",
        "template_switches", "max_concurrent_fronts_10s",
    ]
    with open(out_path, "w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[OK] wrote {out_path} ({len(rows)} rows)")


def try_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        matplotlib.rcParams["svg.fonttype"] = "none"
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        print("[WARN] matplotlib not installed — skipping figure generation. "
              "Run `pip install matplotlib` to enable.")
        return None


def fig_amplification(rows: list[dict], out_dir: Path, plt) -> None:
    cond_rows = {}
    for r in rows:
        cond_rows.setdefault(r["condition"], []).append(r.get("mean_amplification_ratio", 0))
    fig, ax = plt.subplots(figsize=(6, 4))
    labels = list(cond_rows.keys())
    data = [cond_rows[k] for k in labels]
    ax.boxplot(data, labels=labels)
    ax.set_ylabel("mean amplification ratio (atomic orders per NL command)")
    ax.set_title("Capability amplification by condition")
    fig.tight_layout()
    out = out_dir / "fig1_amplification.svg"
    fig.savefig(out)
    plt.close(fig)
    print(f"[OK] wrote {out}")


def fig_apm(rows: list[dict], out_dir: Path, plt) -> None:
    cond_rows = {}
    for r in rows:
        cond_rows.setdefault(r["condition"], []).append(r.get("apm", 0))
    fig, ax = plt.subplots(figsize=(6, 4))
    labels = list(cond_rows.keys())
    data = [cond_rows[k] for k in labels]
    ax.boxplot(data, labels=labels)
    ax.set_ylabel("APM (atomic orders per minute)")
    ax.set_title("Action throughput by condition")
    fig.tight_layout()
    out = out_dir / "fig2_apm.svg"
    fig.savefig(out)
    plt.close(fig)
    print(f"[OK] wrote {out}")


def fig_template_usage(rows: list[dict], out_dir: Path, plt) -> None:
    totals: dict[str, int] = {}
    for r in rows:
        for k, v in (r.get("strategy_template_histogram") or {}).items():
            totals[k] = totals.get(k, 0) + int(v)
    if not totals:
        return
    fig, ax = plt.subplots(figsize=(6, 4))
    labels = list(totals.keys())
    counts = [totals[k] for k in labels]
    ax.bar(labels, counts)
    ax.set_ylabel("times used")
    ax.set_title("Strategy template usage (all human_llm sessions)")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    out = out_dir / "fig5_template_usage.svg"
    fig.savefig(out)
    plt.close(fig)
    print(f"[OK] wrote {out}")


def fig_llm_latency(logs_root: Path, out_dir: Path, plt) -> None:
    """Histogram of llm_latency_ms across all decisions.jsonl lines."""
    latencies: list[int] = []
    for sub in logs_root.iterdir():
        if not sub.is_dir():
            continue
        dec = sub / "decisions.jsonl"
        if not dec.exists():
            continue
        with open(dec, "r", encoding="utf-8") as fp:
            for line in fp:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                lat = e.get("llm_latency_ms")
                if isinstance(lat, (int, float)) and lat > 0:
                    latencies.append(int(lat))
    if not latencies:
        return
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(latencies, bins=30)
    ax.set_xlabel("LLM latency (ms)")
    ax.set_ylabel("count")
    ax.set_title(f"LLM round-trip latency (n={len(latencies)})")
    fig.tight_layout()
    out = out_dir / "fig3_llm_latency.svg"
    fig.savefig(out)
    plt.close(fig)
    print(f"[OK] wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="logs",
                    help="path to logs root (per-session subfolders)")
    ap.add_argument("--out", default="experiments/results",
                    help="output dir for figures + runs.csv")
    args = ap.parse_args()

    logs_root = Path(args.logs)
    out_root = Path(args.out)
    figures_dir = out_root / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    rows = load_sessions(logs_root)
    print(f"[INFO] loaded {len(rows)} session summaries from {logs_root}/")
    if not rows:
        print("[WARN] nothing to aggregate — run experiments first.")
        return

    write_runs_csv(rows, out_root / "runs.csv")

    plt = try_matplotlib()
    if plt:
        fig_amplification(rows, figures_dir, plt)
        fig_apm(rows, figures_dir, plt)
        fig_template_usage(rows, figures_dir, plt)
        fig_llm_latency(logs_root, figures_dir, plt)


if __name__ == "__main__":
    main()
