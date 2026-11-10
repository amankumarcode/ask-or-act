"""Test case format. Structural rules are enforced here; gold SQL is checked by running it."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Label(StrEnum):
    CLEAR = "clear"  # one reasonable reading: the agent should answer
    AMBIGUOUS = "ambiguous"  # several readings with different results: the agent should ask
    UNANSWERABLE = "unanswerable"  # the data cannot answer: the agent should say so


class CompareSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordered: bool = False  # True when row order is part of the answer (ranked lists)
    rel_tol: float = 1e-4  # floats match within max(abs_tol, rel_tol * magnitude)
    abs_tol: float = 0.01  # so rounding to cents passes


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    intent: str  # natural-language meaning; the simulated user holds this
    gold_sql: str


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    __test__ = False  # stop pytest from trying to collect this class

    id: str
    question: str
    label: Label
    db_variant: str
    trap: str | None = None
    interpretations: list[Interpretation] = Field(default_factory=list)
    hidden_intent: str | None = None  # which interpretation the user really means
    naive_sql: str | None = None  # the query that falls for the trap
    reason: str | None = None  # why an unanswerable case is unanswerable
    # What the simulated user means when the case is not ambiguous. Defaults to the clear
    # case's interpretation, or "no more detail" for an unanswerable one.
    user_intent: str | None = None
    tags: list[str] = Field(default_factory=list)
    compare: CompareSpec = Field(default_factory=CompareSpec)
    notes: str | None = None

    @model_validator(mode="after")
    def _check_structure(self) -> "TestCase":
        n = len(self.interpretations)
        ids = [i.id for i in self.interpretations]
        if len(set(ids)) != n:
            raise ValueError(f"{self.id}: duplicate interpretation ids")
        if self.label is Label.CLEAR and n != 1:
            raise ValueError(f"{self.id}: a clear case needs exactly 1 interpretation, has {n}")
        if self.label is Label.AMBIGUOUS:
            if n < 2:
                raise ValueError(f"{self.id}: an ambiguous case needs 2+ interpretations")
            if self.hidden_intent not in ids:
                raise ValueError(f"{self.id}: hidden_intent must be one of {ids}")
        if self.label is Label.UNANSWERABLE:
            if n:
                raise ValueError(f"{self.id}: an unanswerable case has no interpretations")
            if not self.reason:
                raise ValueError(f"{self.id}: an unanswerable case needs a reason")
        return self

    @property
    def target(self) -> Interpretation | None:
        """The interpretation the agent's answer is scored against."""
        if self.label is Label.CLEAR:
            return self.interpretations[0]
        if self.label is Label.AMBIGUOUS:
            return next(i for i in self.interpretations if i.id == self.hidden_intent)
        return None

    @property
    def should_ask(self) -> bool:
        return self.label is Label.AMBIGUOUS

    @property
    def simulated_intent(self) -> str:
        """What the simulated user holds when the agent asks about this case."""
        if self.user_intent:
            return self.user_intent
        if self.target is not None:
            return self.target.intent
        return (
            "You want exactly what the question says. You have no further detail to add and "
            "you don't know what data the company keeps."
        )


class Suite(BaseModel):
    """One YAML file of test cases over one dataset."""

    model_config = ConfigDict(extra="forbid")

    name: str
    dataset: str
    description: str = ""
    default_variant: str | None = None
    cases: list[TestCase]

    @model_validator(mode="before")
    @classmethod
    def _apply_default_variant(cls, data: dict) -> dict:
        default = data.get("default_variant")
        if default:
            for case in data.get("cases", []):
                if isinstance(case, dict):
                    case.setdefault("db_variant", default)
        return data

    @model_validator(mode="after")
    def _unique_ids(self) -> "Suite":
        ids = [c.id for c in self.cases]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"suite {self.name}: duplicate case ids {dupes}")
        return self
