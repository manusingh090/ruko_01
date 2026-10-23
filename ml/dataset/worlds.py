"""Named synthetic worlds, built with data/sim (Nikunj's simulator). Output: ml/data/worlds/<name>/

    PYTHONPATH=backend;ml;data python -m dataset.worlds tiny dev

Training worlds have more scams than real life so the models see enough of them; `calib` and
`test` keep a realistic rate, and every world has its own seed, so no user, mule or journey is
shared between training and evaluation.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from sim.config import SimConfig
from sim.generate import build_world, summary, write_world

ROOT = Path(__file__).resolve().parents[1] / "data" / "worlds"

PRESETS: dict[str, dict] = {
    "tiny": dict(n_users=400, n_days=20, scam_rate=0.05, seed=1),          # tests
    "dev": dict(n_users=5_000, n_days=60, scam_rate=0.04, seed=11),        # quick iterations, training
    "dev_test": dict(n_users=5_000, n_days=60, scam_rate=0.04, seed=12),   # held-out world for dev models
    "dev_device": dict(n_users=5_000, n_days=60, scam_rate=0.04, seed=14),  # trains the on-device model only
    "dev_calib": dict(n_users=5_000, n_days=60, scam_rate=0.04, seed=13),  # threshold tuning (model/tune.py)
    "train_device": dict(n_users=20_000, n_days=60, scam_rate=0.04, seed=21),
    "train_server": dict(n_users=20_000, n_days=60, scam_rate=0.04, seed=22),
    "calib": dict(n_users=20_000, n_days=60, scam_rate=0.01, seed=23),
    "test": dict(n_users=50_000, n_days=60, scam_rate=0.01, seed=24),      # deck slide 7
}


def build(name: str, root: Path = ROOT) -> Path:
    cfg = SimConfig(**PRESETS[name])
    tables = build_world(cfg)
    out = root / name
    write_world(tables, cfg, out)
    print(f"[{name}] {summary(tables)}\n-> {out}")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("names", nargs="+", choices=sorted(PRESETS))
    p.add_argument("--root", type=Path, default=ROOT)
    a = p.parse_args()
    for name in a.names:
        t0 = time.perf_counter()
        build(name, a.root)
        print(f"   {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
