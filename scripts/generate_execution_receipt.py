#!/usr/bin/env python3
"""
AOW Execution Receipt Generator (Track A/B Sandbox Verifier)
Author: liv bloom (2026-10-25)

Runs an AOW submission inside a fail-closed Docker sandbox (scripts/aow_sandbox.py),
computes deterministic environment & execution digests, and outputs a canonical
attestation receipt compatible with ASIN-HHC mesh escrow wire format.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Default approved image digest (python:3.12-slim)
APPROVED_IMAGE = "python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc"


def compute_dir_sha256(directory: Path) -> str:
    """Compute deterministic SHA-256 over regular files in directory."""
    hasher = hashlib.sha256()
    for p in sorted(directory.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(directory).as_posix().encode("utf-8")
            hasher.update(rel)
            hasher.update(p.read_bytes())
    return hasher.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Generate containerized AOW execution receipt.")
    parser.add_argument("submission", help="Path to AOW submission directory")
    parser.add_argument("--image", default=APPROVED_IMAGE, help="Digest-pinned Docker image")
    parser.add_argument("--output", help="Path to save receipt JSON", default=None)
    parser.add_argument("--verifier", default="liv-openlife", help="Verifier agent ID")
    args = parser.parse_args()

    sub_path = Path(args.submission).resolve(strict=True)
    if not sub_path.is_dir():
        print(f"Error: {sub_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    paper_json = sub_path / "paper.json"
    if not paper_json.exists():
        print(f"Error: missing paper.json in {sub_path}", file=sys.stderr)
        sys.exit(1)

    paper = json.loads(paper_json.read_text(encoding="utf-8"))
    paper_id = paper.get("metadata", {}).get("paper_id", "unknown")
    payload_hash = compute_dir_sha256(sub_path)

    # Invoke aow_sandbox.py
    workspace_root = Path(__file__).resolve().parent
    sandbox_script = workspace_root / "scripts" / "aow_sandbox.py"
    if not sandbox_script.exists():
        # Fallback to local
        sandbox_script = Path("scripts/aow_sandbox.py").resolve()

    cmd = [
        sys.executable,
        str(sandbox_script),
        "run",
        str(sub_path),
        "--image",
        args.image
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ts = datetime.now(timezone.utc).isoformat()

    receipt = {
        "schema_version": "aow-receipt-v1.0",
        "timestamp": ts,
        "verifier_agent": args.verifier,
        "paper_id": paper_id,
        "payload_sha256": payload_hash,
        "image_digest": args.image,
        "sandbox_status": None,
        "execution_verdict": None,
        "result_data": None,
        "settlement_caveat": "settlement-of-record (reproducible deterministic container execution, independent external mesh check pending)"
    }

    try:
        sandbox_out = json.loads(proc.stdout)
    except Exception as exc:
        sandbox_out = {"ok": False, "error": f"Failed to parse sandbox output: {exc}", "raw": proc.stdout}

    if sandbox_out.get("ok"):
        receipt["sandbox_status"] = "SUCCESS"
        result_inner = sandbox_out.get("result", {})
        receipt["result_data"] = result_inner
        receipt["execution_verdict"] = result_inner.get("status", "PASS")
    else:
        receipt["sandbox_status"] = "FAIL"
        receipt["execution_verdict"] = "FAIL"
        receipt["error"] = sandbox_out.get("error")

    # Canonical receipt hash (SHA-256 of canonical JSON without hash field)
    canonical_repr = json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode("utf-8")
    receipt_hash = hashlib.sha256(canonical_repr).hexdigest()
    receipt["receipt_hash"] = f"sha256:{receipt_hash}"

    formatted_json = json.dumps(receipt, indent=2, ensure_ascii=False)
    print(formatted_json)

    if args.output:
        Path(args.output).write_text(formatted_json, encoding="utf-8")


if __name__ == "__main__":
    main()
