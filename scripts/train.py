#!/usr/bin/env python3
"""Train models from complete hourly ComEd CSVs or reproducible demo data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sparc_lab.data import demo_dataset, hourly_records, parse_csv_dataset
from sparc_lab.models import get_model_catalog, prepare_chronos, train_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    listing = subparsers.add_parser("list", help="Show honest adapter availability")
    listing.add_argument("--artifacts", default=str(ROOT / "runtime/artifacts"))
    prepare = subparsers.add_parser("prepare-chronos", help="Explicitly download or register official Chronos-Bolt Tiny safetensors")
    prepare.add_argument("--artifacts", default=str(ROOT / "runtime/artifacts"))
    prepare.add_argument("--checkpoint", help="Existing local official checkpoint directory; omit to download")
    train = subparsers.add_parser("train", help="Chronologically train, validate and test an actual model")
    train.add_argument("model", choices=["ridge", "lightgbm", "chronos-bolt"])
    source = train.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="Use clearly synthetic example data")
    source.add_argument("--csv", type=Path, help="Interval-start five-minute CSV following docs/DATA.md")
    train.add_argument("--artifacts", default=str(ROOT / "runtime/artifacts"))
    train.add_argument("--train-fraction", type=float, default=0.7)
    train.add_argument("--horizon-hours", type=int, default=24)
    train.add_argument("--stride-hours", type=int, default=6)
    train.add_argument("--epochs", type=int, default=3, help="Chronos only")
    train.add_argument("--batch-size", type=int, default=8, help="Chronos only")
    train.add_argument("--context-hours", type=int, default=168, help="Chronos only")
    train.add_argument("--device", choices=["cpu", "cuda", "mps"], default="cpu", help="Chronos only")
    train.add_argument("--learning-rate", type=float, default=1e-5, help="Chronos only")
    arguments = parser.parse_args()
    try:
        if arguments.command == "list":
            result = get_model_catalog(arguments.artifacts)
        elif arguments.command == "prepare-chronos":
            result = prepare_chronos(arguments.artifacts, arguments.checkpoint)
        else:
            dataset = demo_dataset() if arguments.demo else parse_csv_dataset(arguments.csv.read_text(encoding="utf-8-sig"), arguments.csv.stem)
            config = {key: getattr(arguments, key) for key in ["horizon_hours", "stride_hours", "epochs", "batch_size", "context_hours", "device", "learning_rate"]}
            result = train_model(arguments.model, hourly_records(dataset), arguments.artifacts, arguments.train_fraction, config)
            result["training_data"] = {"name": dataset["name"], "is_synthetic": dataset["is_synthetic"]}
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, OSError, ImportError) as error:
        parser.exit(2, f"Training error: {error}\n")


if __name__ == "__main__":
    main()
