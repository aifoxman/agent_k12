"""python -m scripts.question_bank --help"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.question_bank.budget import Budget
from scripts.question_bank.corpus import backfill, choose_documents, classify_cached, dump, inventory, read

ROOT = Path(__file__).resolve().parents[2]


def run_directory(value):
    path = Path(value).resolve()
    allowed = (ROOT / "output" / "acceptance").resolve()
    if path == allowed or not path.is_relative_to(allowed):
        raise argparse.ArgumentTypeError("Use a private run under output/acceptance")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=run_directory)
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index")
    index.add_argument("--source", required=True, type=Path)
    commands.add_parser("snapshot")
    commands.add_parser("freeze-final")
    commands.add_parser("export")
    commands.add_parser("backfill")
    commands.add_parser("classify")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--limit", type=int, default=1000)
    seal = commands.add_parser("seal")
    seal.add_argument("--font", default="C:/Windows/Fonts/msyh.ttc")
    for command in ("run", "review"):
        sub = commands.add_parser(command)
        sub.add_argument("--round", type=int, choices=(1, 2, 3), required=True)
        sub.add_argument("--tag", required=True)
        sub.add_argument("--split", choices=("development", "holdout"), default="development")
        sub.add_argument("--limit", type=int, default=300)
        if command == "run":
            sub.add_argument("--base-url", required=True)
            sub.add_argument("--api-run", type=run_directory, required=True)
            sub.add_argument("--case", action="append")
            sub.add_argument("--modes", nargs="+", choices=("hint1", "hint2", "correct", "wrong", "blank"))
            sub.add_argument("--variant", choices=("clean", "rotation", "perspective", "shadow", "blur", "jpeg", "movement"), default="clean")
            sub.add_argument("--audio", action="store_true")
            sub.add_argument("--cache", choices=("cold", "warm"), default="cold")
    summary = commands.add_parser("report")
    summary.add_argument("--tag", required=True)
    summary.add_argument("--split", choices=("development", "holdout"), default="development")
    comparison = commands.add_parser("compare")
    comparison.add_argument("--baseline", required=True)
    comparison.add_argument("--candidate", required=True)
    comparison.add_argument("--split", choices=("development", "holdout"), default="development")
    commands.add_parser("budget")
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "src"))
    if args.command == "index":
        value = inventory(args.source, output)
        records = choose_documents(value)
        dump(output / "private" / "selected-documents.json", records)
        print(json.dumps({"files": len(value["records"]), "selected": len(records), "errors": len(value["errors"])}))
    elif args.command == "backfill":
        print(json.dumps(backfill(output)))
    elif args.command == "classify":
        print(json.dumps(classify_cached(output)))
    elif args.command == "snapshot":
        destination = output / "private" / "baseline-src"
        if destination.exists():
            raise ValueError("Baseline snapshot already exists")
        shutil.copytree(ROOT / "src" / "k12_server", destination / "k12_server", ignore=shutil.ignore_patterns("__pycache__"))
        dump(destination / "source.json", {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                                          "agents_sha256": __import__("hashlib").sha256((ROOT / "AGENTS.md").read_bytes()).hexdigest()})
        print("Baseline source frozen")
    elif args.command == "freeze-final":
        from scripts.question_bank.seal import freeze
        print(json.dumps(freeze(output, ROOT / "src" / "k12_server")))
    elif args.command == "export":
        records = read(output / "private" / "selected-documents.json")
        root = Path(read(output / "private" / "inventory.json")["source_root"])
        (output / "private" / "pdf").mkdir(parents=True, exist_ok=True)
        destination = output / "private" / "word-export.json"
        dump(destination, [{"id": r["sha256"][:20], "source": str(root / r["path"]),
                            "pdf": str(output / "private" / "pdf" / f"{r['sha256']}.pdf")} for r in records])
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(Path(__file__).with_name("export-word.ps1")), "-Manifest", str(destination)], check=True)
    elif args.command == "prepare":
        from scripts.question_bank.prepare import prepare_all
        from scripts.question_bank.teacher import load_settings
        prepare_all(output, load_settings(), limit=args.limit)
    elif args.command == "seal":
        from scripts.question_bank.prepare import make_manifest
        print(json.dumps(make_manifest(output, args.font)))
    elif args.command == "run":
        from scripts.question_bank.runner import run
        run(output, args.base_url, args.api_run, args.round, args.tag, split=args.split,
            modes=args.modes, selected=args.case, limit=args.limit, variant=args.variant, audio=args.audio, cache=args.cache)
    elif args.command == "review":
        from scripts.question_bank.runner import review
        from scripts.question_bank.teacher import load_settings
        review(output, load_settings(), args.round, args.tag, split=args.split, limit=args.limit)
    elif args.command == "report":
        from scripts.question_bank.report import report
        result = report(output, args.tag, args.split)
        print(json.dumps({"groups": result["groups"].get("all"), "report": args.tag}))
    elif args.command == "compare":
        from scripts.question_bank.report import compare
        print(json.dumps(compare(output, args.baseline, args.candidate, args.split)))
    elif args.command == "budget":
        print(json.dumps(Budget(output / "budget.sqlite3").summary()))
    return 0
