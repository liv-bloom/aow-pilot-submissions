"""
---
name: aow-sandbox
description: Run an AOW reproduction entrypoint inside a fail-closed Docker sandbox.
generator: developer-daemon
created: 2026-09-28
---
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT = 60
DEFAULT_MAX_FILES = 2_000
DEFAULT_MAX_INPUT_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_RESULT_BYTES = 1 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 16 * 1024


class SandboxError(Exception):
    pass


def emit(payload: dict[str, Any], exit_code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return exit_code


def docker_status() -> dict[str, Any]:
    docker = shutil.which("docker")
    if docker is None:
        return {"ok": False, "docker_binary": None, "daemon_reachable": False}
    try:
        probe = subprocess.run(
            [docker, "info", "--format", "{{json .ServerVersion}}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except subprocess.SubprocessError as exc:
        return {
            "ok": False,
            "docker_binary": docker,
            "daemon_reachable": False,
            "error": str(exc),
        }
    return {
        "ok": probe.returncode == 0,
        "docker_binary": docker,
        "daemon_reachable": probe.returncode == 0,
        "server_version": probe.stdout.decode("utf-8", "replace").strip()
        if probe.returncode == 0
        else None,
        "error": probe.stderr[:MAX_DIAGNOSTIC_BYTES]
        .decode("utf-8", "replace")
        .strip()
        if probe.returncode != 0
        else None,
    }


def load_entrypoint(submission: Path) -> str:
    paper_path = submission / "paper.json"
    try:
        paper = json.loads(paper_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SandboxError(f"cannot read valid paper.json: {exc}") from exc
    if not isinstance(paper, dict):
        raise SandboxError("paper.json must contain a JSON object")
    reproducibility = paper.get("reproducibility")
    if not isinstance(reproducibility, dict):
        raise SandboxError("paper.json has no valid reproducibility object")
    entrypoint = reproducibility.get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise SandboxError("paper.json has no valid reproducibility.entrypoint")
    if "\x00" in entrypoint:
        raise SandboxError("entrypoint contains a NUL byte")
    root = submission.resolve(strict=True)
    candidate = (root / entrypoint).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SandboxError("entrypoint escapes the submission directory") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise SandboxError("entrypoint is not a regular in-tree file")
    return candidate.relative_to(root).as_posix()


def validate_tree(root: Path, max_files: int, max_bytes: int) -> None:
    files = 0
    total = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [*dirs, *names]:
            path = current_path / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise SandboxError(f"symlink rejected: {path.relative_to(root)}")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise SandboxError(f"special file rejected: {path.relative_to(root)}")
            if stat.S_ISREG(mode):
                files += 1
                total += path.stat().st_size
                if files > max_files:
                    raise SandboxError(f"submission exceeds {max_files} files")
                if total > max_bytes:
                    raise SandboxError(f"submission exceeds {max_bytes} bytes")


def make_staged_copy(
    source: Path, destination: Path, max_files: int, max_bytes: int
) -> None:
    # Preserve links during the copy and reject them in the staged tree. This
    # avoids following a link that appears between the source validation and
    # copy (a small but meaningful TOCTOU boundary).
    shutil.copytree(source, destination, symlinks=True)
    validate_tree(destination, max_files, max_bytes)
    for current, dirs, names in os.walk(destination):
        os.chmod(current, 0o555)
        for name in dirs:
            os.chmod(Path(current) / name, 0o555)
        for name in names:
            os.chmod(Path(current) / name, 0o444)


def read_result(path: Path, max_bytes: int) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise SandboxError(f"missing or unsafe /out/result.json: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SandboxError("/out/result.json is not a regular file")
        if info.st_size > max_bytes:
            raise SandboxError(f"/out/result.json exceeds {max_bytes} bytes")
        raw = os.read(descriptor, max_bytes + 1)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SandboxError(f"/out/result.json is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SandboxError("/out/result.json must contain a JSON object")
    return value


def run_submission(args: argparse.Namespace) -> dict[str, Any]:
    if "@sha256:" not in args.image:
        raise SandboxError("--image must be pinned with @sha256:<digest>")
    source = Path(args.submission).resolve(strict=True)
    if not source.is_dir():
        raise SandboxError("submission must be a directory")
    validate_tree(source, args.max_files, args.max_input_bytes)
    entrypoint = load_entrypoint(source)
    status = docker_status()
    if not status["ok"]:
        raise SandboxError(f"Docker is unavailable: {status.get('error') or 'not installed'}")

    docker = status["docker_binary"]
    container_name = f"aow-sandbox-{os.getpid()}-{secrets.token_hex(4)}"
    with tempfile.TemporaryDirectory(prefix="aow-sandbox-") as temp:
        temp_root = Path(temp)
        staged = temp_root / "submission"
        output = temp_root / "output"
        make_staged_copy(source, staged, args.max_files, args.max_input_bytes)
        output.mkdir()
        # mkdir's mode is filtered by the host umask; set this explicitly so
        # the unprivileged container user can create the protocol result.
        output.chmod(0o777)
        command = [
            docker,
            "run",
            "--pull",
            "never",
            "--name",
            container_name,
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--memory",
            args.memory,
            "--cpus",
            args.cpus,
            "--pids-limit",
            str(args.pids),
            "--ulimit",
            "nofile=256:256",
            "--log-driver",
            "none",
            "--mount",
            f"type=bind,src={staged},dst=/work,readonly",
            "--mount",
            f"type=bind,src={output},dst=/out",
            "--workdir",
            "/work",
            "--env",
            "HOME=/tmp",
            args.image,
            "bash",
            "--noprofile",
            "--norc",
            "--",
            f"./{entrypoint}",
        ]
        diagnostic = tempfile.TemporaryFile()
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=diagnostic,
                timeout=args.timeout,
            )
            return_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            return_code = None
        finally:
            subprocess.run(
                [docker, "rm", "--force", container_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        diagnostic.seek(0)
        error_text = diagnostic.read(MAX_DIAGNOSTIC_BYTES).decode("utf-8", "replace")
        diagnostic.close()
        if timed_out:
            raise SandboxError(f"container exceeded {args.timeout}s timeout")
        if return_code != 0:
            raise SandboxError(
                f"container exited with status {return_code}: {error_text.strip()}"
            )
        result = read_result(output / "result.json", args.max_result_bytes)
        return {
            "ok": True,
            "container_exit_code": return_code,
            "entrypoint": entrypoint,
            "image": args.image,
            "result": result,
        }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Run an AOW reproduction entrypoint in a fail-closed Docker sandbox."
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="check Docker availability without pulling images")
    run = commands.add_parser("run", help="run one submission in the sandbox")
    run.add_argument("submission")
    run.add_argument("--image", required=True, help="digest-pinned Docker image")
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    run.add_argument("--memory", default="512m")
    run.add_argument("--cpus", default="1.0")
    run.add_argument("--pids", type=int, default=128)
    run.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    run.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    run.add_argument("--max-result-bytes", type=int, default=DEFAULT_MAX_RESULT_BYTES)
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "check":
        status = docker_status()
        return emit(status, 0 if status["ok"] else 1)
    try:
        return emit(run_submission(args), 0)
    except (SandboxError, OSError, subprocess.SubprocessError) as exc:
        return emit({"ok": False, "error": str(exc)}, 2)


if __name__ == "__main__":
    sys.exit(main())
