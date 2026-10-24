#!/usr/bin/env python3
"""
AOW peer署名の受領→検証→記録を1コマンドで行うCLI。
作成: sami (2026-10-16)

使い方:
    python3 verify_peer_sig_cli.py \
        --root-hash <hash> --author sami \
        --peer liv --sig-file liv_sig.txt \
        --peer uro --sig-file uro_sig.txt \
        --min-k 2

sig-fileにはliv/uroから届いたSSHSIGブロック(-----BEGIN SSH SIGNATURE-----...)
をそのまま保存しておく。peer_keys.pyのregistryと突き合わせて検証し、
結果を attestation_log.jsonl に1行追記する。
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from peer_keys import aow_peer_registry  # noqa: E402
from peer_signature_sshsig import aggregate_peer_signatures_sshsig  # noqa: E402

LOG_PATH = Path(__file__).parent / "attestation_log.jsonl"


def main():
    ap = argparse.ArgumentParser(description="AOW peer署名の検証とk=2閾値記録")
    ap.add_argument("--root-hash", required=True)
    ap.add_argument("--author", required=True, help="author_agent_id (例: sami)")
    ap.add_argument("--min-k", type=int, default=2)
    ap.add_argument(
        "--entry",
        action="append",
        nargs=2,
        metavar=("PEER_ID", "SIG_FILE"),
        required=True,
        help="--entry liv liv_sig.txt のように複数指定可",
    )
    args = ap.parse_args()

    peer_signatures = []
    for peer_id, sig_file in args.entry:
        sig_text = Path(sig_file).read_text()
        peer_signatures.append({"peer_agent_id": peer_id, "signature": sig_text})

    result = aggregate_peer_signatures_sshsig(
        peer_signatures,
        root_hash=args.root_hash,
        author_agent_id=args.author,
        registry=aow_peer_registry,
        min_k=args.min_k,
    )

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "root_hash": args.root_hash,
        "author_agent_id": args.author,
        "min_k": args.min_k,
        **result,
    }

    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"\n{'PASS' if result['pass'] else 'FAIL'} — logged to {LOG_PATH}")
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
