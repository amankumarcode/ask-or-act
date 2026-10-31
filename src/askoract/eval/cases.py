"""Test case format. Structural rules are enforced here; gold SQL is checked by running it."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Label(StrEnum):
    CLEAR = "clear"  # one reasonable reading: the agent should answer
    AMBIGUOUS = "ambiguous"  # several readings with different results: the agent should ask
    UNANSWERABLE = "unanswerable"  # the data cannot answer: the agent should say so


class CompareSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordered: bool = False  # True when row order is part of the answer (top-N lists)
    float_tol: float = 0.01  # relative tolerance for numeric cells


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
