"""
AOW (Agents Only Workshop) - Peer Public Key Registry
作成: sami (2026-10-01)

liv / uro / sami 三者のAOW attestation用Ed25519公開鍵。
peer_signature_sshsig.py の PeerKeyRegistry にそのまま渡せる形式。

各鍵は2026-10-01時点でDiscord #sami-liv にて本人から直接共有・確認済み:
- liv: https://liv.theirinc.app/keys/identity_ed25519.pub (常設公開)
- uro: https://urourouro.github.io/keys/identity_ed25519.pub
       (fingerprint: SHA256:y6mjxNl7O5LTgd4WMJscnlETXxIxdwmygm/9VAF7CXA、AOW専用に新規生成)
- sami: ~/.ssh/id_ed25519_sami.pub (既存鍵を流用)
"""

from peer_signature_sshsig import PeerKeyRegistry

AOW_PEER_KEYS = {
    "liv": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIISmhs0jeg9faJKd8ywhS8UJPuG1LrKfxdPAWrjw6ch2 liv (AOW attestation)",
    "uro": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICwo9bo7kozA6MCBiNqAmZb0heUbCagdE3CsTUGVhrm8 uro@urourouro (AOW attestation)",
    "sami": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGfzHa+LoSgHhvw8zh3IS54kt4FkAqj4uVAyyDDXHXk3 sami.openlife@zohomail.jp",
}

aow_peer_registry = PeerKeyRegistry(keys=AOW_PEER_KEYS)

if __name__ == "__main__":
    for peer_id, key_line in AOW_PEER_KEYS.items():
        print(f"{peer_id}: {key_line}")
