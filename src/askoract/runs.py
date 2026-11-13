"""Run directories: one per `ask` or `eval` invocation.

runs/<run_id>/manifest.json      what ran: git sha, settings, profiles, prompt hashes
runs/<run_id>/traces/<case>.jsonl  every event of one question
runs/latest -> <run_id>
"""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from askoract.config import AgentProfile, Settings
from askoract.tracing.tracer import JsonlSink, Sink, Tracer

LATEST = "latest"


@dataclass(frozen=True)
class Run:
    id: str
    dir: Path

    def trace_path(self, case_id: str) -> Path:
        return self.dir / "traces" / f"{case_id}.jsonl"

    def tracer(self, case_id: str, *, sinks: list[Sink] | None = None, **context: Any) -> Tracer:
        return Tracer(
            [JsonlSink(self.trace_path(case_id)), *(sinks or [])],
            run_id=self.id,
            case_id=case_id,
            **context,
        )


def _git_state(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=5, check=True
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain")
    return {"sha": git("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None}


def prompt_fingerprints(settings: Settings, profiles: list[AgentProfile]) -> dict[str, str]:
    """sha256 (short) of every prompt file the run uses, keyed by its name."""
    files = {f"{p.prompt}.md" for p in profiles} | {f"{p.tool_descriptions}.yaml" for p in profiles}
    files.add(f"{settings.simulated_user.prompt}.md")
    out = {}
    for rel in sorted(files):
        path = settings.paths.prompts_dir / rel
        if path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return out


def new_run(
    settings: Settings,
    kind: str,
    profiles: list[AgentProfile],
    extra: dict[str, Any] | None = None,
) -> Run:
    runs_dir = settings.paths.runs_dir
    runs_dir.mkdir(parents=True, exist_ok=True)
    base = f"{kind}-{datetime.now():%Y%m%d-%H%M%S}"
    run_id, n = base, 1
    while (runs_dir / run_id).exists():
        n += 1
        run_id = f"{base}-{n}"
    run_dir = runs_dir / run_id
    (run_dir / "traces").mkdir(parents=True)

    manifest = {
        "run_id": run_id,
        "kind": kind,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git": _git_state(settings.root),
        "seed": settings.seed,
        "profiles": [p.model_dump(mode="json") for p in profiles],
        "simulated_user": settings.simulated_user.model_dump(mode="json"),
        "prompts": prompt_fingerprints(settings, profiles),
        **(extra or {}),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    latest = runs_dir / LATEST
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    os.symlink(run_id, latest)
    return Run(run_id, run_dir)


def resolve_run(settings: Settings, run_id: str = LATEST) -> Run:
    path = settings.paths.runs_dir / run_id
    if not path.exists():
        raise FileNotFoundError(f"no run {run_id!r} in {settings.paths.runs_dir}")
    resolved = path.resolve()
    return Run(resolved.name, resolved)


def manifest(run: Run) -> dict[str, Any]:
    return json.loads((run.dir / "manifest.json").read_text())
