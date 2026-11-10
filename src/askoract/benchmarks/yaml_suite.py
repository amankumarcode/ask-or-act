"""Benchmark backed by a YAML suite file: datasets/<dataset>/suites/*.yaml.

Each case runs on its dataset variant, and clarifying questions go to a simulated user
holding the case's hidden intent.
"""

from pathlib import Path

import yaml

from askoract.benchmarks.base import CaseEnv
from askoract.config import Settings
from askoract.datasets import get_dataset
from askoract.db import Database
from askoract.eval.cases import Suite, TestCase
from askoract.factory import make_simulated_user
from askoract.llm.base import LLMClient
from askoract.tracing.tracer import Tracer


def find_suites(settings: Settings) -> dict[str, Path]:
    """Suite name -> file, for every suite file under the datasets directory."""
    out: dict[str, Path] = {}
    for path in sorted(settings.paths.datasets_dir.glob("*/suites/*.yaml")):
        name = (yaml.safe_load(path.read_text()) or {}).get("name")
        if name:
            out[name] = path
    return out


def load_suite(settings: Settings, name: str) -> Suite:
    suites = find_suites(settings)
    if name not in suites:
        raise KeyError(f"unknown suite {name!r}; available: {', '.join(suites) or '(none)'}")
    return Suite(**yaml.safe_load(suites[name].read_text()))


class YamlSuiteBenchmark:
    def __init__(
        self, settings: Settings, suite: Suite, *, user_llm: LLMClient | None = None
    ) -> None:
        self.settings = settings
        self.suite = suite
        self.name = suite.name
        self.dataset = get_dataset(settings, suite.dataset)
        self._user_llm = user_llm  # tests inject a fake; normally built from config

    def cases(self) -> list[TestCase]:
        return list(self.suite.cases)

    def open_db(self, variant: str) -> Database:
        return self.dataset.open(variant)

    def environment(self, case: TestCase, tracer: Tracer) -> CaseEnv:
        user = make_simulated_user(
            case.question, case.simulated_intent, self.settings, tracer=tracer, llm=self._user_llm
        )
        return CaseEnv(db=self.open_db(case.db_variant), user=user)
