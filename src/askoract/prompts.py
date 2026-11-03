"""Load versioned prompt files from prompts/ and fill their ${placeholders}.

`string.Template` is used instead of str.format so that braces in prompt text
(JSON or SQL examples) need no escaping.
"""

from pathlib import Path
from string import Template
from typing import Any

import yaml

from askoract.config import Settings


def _path(settings: Settings, name: str, suffix: str) -> Path:
    path = settings.paths.prompts_dir / f"{name}{suffix}"
    if not path.is_file():
        raise FileNotFoundError(f"prompt file not found: {path}")
    return path


def render(template: str, **values: Any) -> str:
    return Template(template).safe_substitute({k: str(v) for k, v in values.items()})


def load_prompt(settings: Settings, name: str, **values: Any) -> str:
    """e.g. load_prompt(settings, "agent/full_v1", dialect="sqlite")"""
    return render(_path(settings, name, ".md").read_text(), **values).strip()


def load_tool_descriptions(settings: Settings, version: str, **values: Any) -> dict[str, str]:
    """e.g. load_tool_descriptions(settings, "tools/v1", dialect="sqlite", max_rows=200)"""
    raw = yaml.safe_load(_path(settings, version, ".yaml").read_text()) or {}
    return {name: render(text, **values).strip() for name, text in raw.items()}
