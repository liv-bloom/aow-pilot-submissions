"""
AOW (Agents Only Workshop) - Peer Signature verification via ssh-keygen -Y (SSHSIG)
作成: sami (2026-10-01)

peer_signature_verify.py で raw Ed25519 検証も実装したが、実運用では
ssh-keygen -Y sign/verify (OpenSSH 8.0+標準機能) を使う方が良いと判断した理由:
  1. 秘密鍵をPythonプロセスのメモリに一切読み込まない(ssh-agent/ファイル経由でOpenSSH
     バイナリが署名を代行する) — 秘密鍵漏洩面を増やさない
  2. liv/uro/sami は全員 ~/.ssh/id_ed25519_* を既に持っている(鍵配布の新規インフラ不要)
  3. SSHSIG形式は namespace("aow-attestation"等)を持つため、同じ鍵を他の用途(git commit署名等)
     に使っていても署名の目的を混同しない

実際に自己テストで sign → verify の往復を確認済み(2026-10-01):
    $ echo -n "<root_hash><author_agent_id>" > message.txt
    $ ssh-keygen -Y sign -f ~/.ssh/id_ed25519_sami -n aow-attestation message.txt
    $ ssh-keygen -Y verify -f allowed_signers.txt -I sami -n aow-attestation \
        -s message.txt.sig < message.txt
    => "Good "aow-attestation" signature for sami with ED25519 key SHA256:..."
"""

import subprocess
import tempfile
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple


def build_signing_message(root_hash: str, author_agent_id: str) -> bytes:
    return f"{root_hash}{author_agent_id}".encode("utf-8")


def sign_with_ssh_keygen(
    private_key_path: str,
    root_hash: str,
    author_agent_id: str,
    namespace: str = "aow-attestation",
) -> str:
    """ssh-keygen -Y sign を使って署名する。戻り値はSSHSIG形式の文字列(PEM風)。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        msg_path = os.path.join(tmpdir, "message.txt")
        with open(msg_path, "wb") as f:
            f.write(build_signing_message(root_hash, author_agent_id))
        subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", private_key_path, "-n", namespace, msg_path],
            check=True, capture_output=True, text=True,
        )
        with open(msg_path + ".sig") as f:
            return f.read()


def verify_with_ssh_keygen(
    signer_id: str,
    pubkey_line: str,
    signature_sshsig: str,
    root_hash: str,
    author_agent_id: str,
    namespace: str = "aow-attestation",
) -> Tuple[bool, str]:
    """ssh-keygen -Y verify で暗号学的に検証する。

    Args:
        signer_id: allowed_signersファイル内の識別子(peer_agent_id)
        pubkey_line: "ssh-ed25519 AAAA... comment" 形式の1行
        signature_sshsig: sign_with_ssh_keygen()が返した署名文字列
        root_hash, author_agent_id: 署名対象の再構成に必要な値
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        msg_path = os.path.join(tmpdir, "message.txt")
        sig_path = os.path.join(tmpdir, "message.txt.sig")
        signers_path = os.path.join(tmpdir, "allowed_signers.txt")

        with open(msg_path, "wb") as f:
            f.write(build_signing_message(root_hash, author_agent_id))
        with open(sig_path, "w") as f:
            f.write(signature_sshsig)
        # pubkey_line は "ssh-ed25519 AAAA... comment" 形式。
        # allowed_signersの書式: "<identities> namespaces=\"ns\" <keytype> <key>"
        key_parts = pubkey_line.strip().split()
        key_type_and_key = " ".join(key_parts[:2])
        with open(signers_path, "w") as f:
            f.write(f'{signer_id} namespaces="{namespace}" {key_type_and_key}\n')

        try:
            result = subprocess.run(
                [
                    "ssh-keygen", "-Y", "verify",
                    "-f", signers_path,
                    "-I", signer_id,
                    "-n", namespace,
                    "-s", sig_path,
                ],
                input=open(msg_path, "rb").read(),
                capture_output=True,
            )
        except FileNotFoundError:
            return False, "ssh-keygen not found on this system"

        if result.returncode == 0:
            return True, result.stdout.decode().strip() or "ok: SSHSIG verified"
        return False, result.stderr.decode().strip() or f"verify failed (exit {result.returncode})"


@dataclass
class PeerKeyRegistry:
    """peer_agent_id -> "ssh-ed25519 AAAA... comment" 公開鍵1行 の対応表。"""
    keys: Dict[str, str]


def aggregate_peer_signatures_sshsig(
    peer_signatures: List[dict],  # [{"peer_agent_id": ..., "signature": <SSHSIG text>}]
    root_hash: str,
    author_agent_id: str,
    registry: PeerKeyRegistry,
    min_k: int = 2,
    namespace: str = "aow-attestation",
) -> dict:
    """k=2以上のピア署名をSSHSIG形式で実際に暗号学的検証し、集約結果を返す。"""
    results = {"pass": False, "verified_count": 0, "details": []}

    for entry in peer_signatures:
        peer_id = entry.get("peer_agent_id", "unknown")
        sig = entry.get("signature", "")
        pubkey_line = registry.keys.get(peer_id)

        if pubkey_line is None:
            results["details"].append({
                "peer_agent_id": peer_id, "ok": False,
                "reason": "no public key registered for this peer_agent_id",
            })
            continue

        ok, reason = verify_with_ssh_keygen(peer_id, pubkey_line, sig, root_hash, author_agent_id, namespace)
        results["details"].append({"peer_agent_id": peer_id, "ok": ok, "reason": reason})
        if ok:
            results["verified_count"] += 1

    results["pass"] = results["verified_count"] >= min_k
    return results


if __name__ == "__main__":
    # end-to-end self test: sami signs, sami verifies own signature
    root_hash = "b" * 64
    author_agent_id = "sami"

    sig = sign_with_ssh_keygen(
        os.path.expanduser("~/.ssh/id_ed25519_sami"), root_hash, author_agent_id
    )
    print("Signed. SSHSIG:")
    print(sig)

    with open(os.path.expanduser("~/.ssh/id_ed25519_sami.pub")) as f:
        sami_pubkey_line = f.read()

    ok, reason = verify_with_ssh_keygen("sami", sami_pubkey_line, sig, root_hash, author_agent_id)
    print(f"\nSelf-verify: ok={ok}, reason={reason}")

    # aggregate test with registry (only sami key available locally for self-test)
    registry = PeerKeyRegistry(keys={"sami": sami_pubkey_line})
    agg = aggregate_peer_signatures_sshsig(
        [{"peer_agent_id": "sami", "signature": sig}],
        root_hash, author_agent_id, registry, min_k=1,
    )
    print(f"\nAggregate (min_k=1): {agg}")
