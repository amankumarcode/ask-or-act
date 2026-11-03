"""The seven agent tools, called directly with no LLM."""

import pytest

from askoract import api
from askoract.config import Limits, load_profile, load_settings
from askoract.tools import TOOLS, ToolContext
from askoract.tools.formatting import format_table, format_value
from askoract.users import ScriptedUser

BUILTIN = [
    "list_tables",
    "describe_table",
    "sample_rows",
    "get_column_stats",
    "run_sql",
    "ask_user",
    "final_answer",
]


@pytest.fixture
def ctx(db):
    return ToolContext(db=db, user=ScriptedUser("Net of discount."), limits=Limits(sql_max_rows=3))


def call(name, args, ctx):
    return TOOLS.get(name)("test description")(args, ctx)


# -- formatting ------------------------------------------------------------------


def test_format_value():
    assert format_value(None) == "NULL"
    assert format_value(b"\x00" * 10) == "<blob 10 bytes>"
    assert format_value(2407537.5492) == "2407537.5492"  # never scientific
    assert format_value(16.0) == "16"
    assert format_value("x" * 200).endswith("…") and len(format_value("x" * 200)) == 80


def test_format_table_respects_char_budget():
    out = format_table(["a"], [(i,) for i in range(1000)], max_chars=50)
    assert "output cut" in out and len(out) < 120


# -- schema tools ------------------------------------------------------------------


def test_list_tables(ctx):
    res = call("list_tables", {}, ctx)
    assert "3 tables, 1 views" in res.content
    assert "Order Details" in res.content
    assert {"name": "German", "kind": "view", "rows": None} in res.data["tables"]


def test_describe_table_shows_keys_and_comments(ctx):
    res = call("describe_table", {"table": "customers"}, ctx)
    assert res.content.startswith("Customers  -- People")
    assert "PK" in res.content and "-- ISO" in res.content
    fk = call("describe_table", {"table": "Order Details"}, ctx)
    assert "-> Customers.CustomerID" in fk.content


def test_describe_unknown_table_is_recoverable(ctx):
    res = call("describe_table", {"table": "Customer"}, ctx)
    assert res.is_error and "Did you mean: Customers" in res.content


def test_sample_rows_spread_and_blob_safe(ctx):
    res = call("sample_rows", {"table": "Customers", "n": 2}, ctx)
    assert "2 of 4 rows" in res.content
    assert [r[0] for r in res.data["rows"]] == ["A", "C"]  # offsets 0 and 2
    blob = call("sample_rows", {"table": "Files"}, ctx)
    assert "<blob 4 bytes>" in blob.content


def test_sample_rows_rejects_large_n(ctx):
    res = call("sample_rows", {"table": "Customers", "n": 500}, ctx)
    assert res.is_error and "n" in res.content


def test_column_stats(ctx):
    res = call("get_column_stats", {"table": "Customers", "column": "country"}, ctx)
    assert res.data["nulls"] == 1 and res.data["distinct"] == 2
    assert res.data["top"][0] == ["Germany", 2]
    assert "-- ISO" in res.content
    num = call("get_column_stats", {"table": "Files", "column": "price"}, ctx)
    assert "avg: 2407537.5492" in num.content


def test_column_stats_unknown_column_suggests(ctx):
    res = call("get_column_stats", {"table": "Customers", "column": "Countr"}, ctx)
    assert res.is_error and "Did you mean: Country" in res.content


# -- run_sql -------------------------------------------------------------------------


def test_run_sql_rows_and_truncation(ctx):
    res = call("run_sql", {"sql": "SELECT CustomerID FROM Customers ORDER BY 1"}, ctx)
    assert res.data["truncated"] and res.data["row_count"] == 3
    assert "more exist" in res.content
    one = call("run_sql", {"sql": "SELECT COUNT(*) AS n FROM Customers"}, ctx)
    assert one.content.endswith("(1 row)") and one.data["rows"] == [[4]]


def test_run_sql_empty_result(ctx):
    res = call("run_sql", {"sql": "SELECT * FROM Customers WHERE 0"}, ctx)
    assert "(0 rows)" in res.content and not res.is_error


def test_run_sql_rejects_writes(ctx):
    res = call("run_sql", {"sql": "DELETE FROM Customers"}, ctx)
    assert res.is_error and "read-only" in res.content


def test_run_sql_hints_at_unquoted_space_names(ctx):
    res = call("run_sql", {"sql": "SELECT * FROM Order Details"}, ctx)
    assert res.is_error and 'double-quoted, e.g. "Order Details"' in res.content
    ok_but_wrong = call("run_sql", {"sql": 'SELECT * FROM "Order Details" WHERE nope = 1'}, ctx)
    assert "Hint" not in ok_but_wrong.content


# -- interaction ---------------------------------------------------------------------


def test_ask_user_records_and_enforces_limit(ctx):
    ctx.limits = Limits(max_clarifications=1)
    first = call("ask_user", {"question": "Gross or net revenue?"}, ctx)
    assert first.content == "User replied: Net of discount."
    assert ctx.clarifications == [("Gross or net revenue?", "Net of discount.")]
    second = call("ask_user", {"question": "And freight?"}, ctx)
    assert second.is_error and "already asked 1" in second.content
    assert ctx.user.questions == ["Gross or net revenue?"]


def test_final_answer_answered(ctx):
    tool = TOOLS.get("final_answer")("d")
    assert tool.terminal
    res = tool({"status": "answered", "answer": "4", "sql": "SELECT COUNT(*) FROM Customers"}, ctx)
    assert res.final is not None and res.final.answer == "4" and not res.is_error


@pytest.mark.parametrize(
    "args, message",
    [
        ({"status": "answered", "answer": "4"}, "sql is required"),
        ({"status": "answered", "answer": "4", "sql": "SELEC 1"}, "does not run"),
        ({"status": "maybe", "answer": "4"}, "status"),
    ],
)
def test_final_answer_rejects_bad_submissions(ctx, args, message):
    res = call("final_answer", args, ctx)
    assert res.is_error and message in res.content and res.final is None


def test_final_answer_cannot_answer_needs_no_sql(ctx):
    res = call("final_answer", {"status": "cannot_answer", "answer": "No spend data."}, ctx)
    assert res.final.status == "cannot_answer"


# -- toolsets and descriptions ----------------------------------------------------------


def test_all_builtin_tools_registered():
    assert set(BUILTIN) <= set(TOOLS.names())


def test_toolset_from_profile_renders_descriptions(repo_root):
    settings = load_settings(repo_root)
    full = api.build_toolset(load_profile(settings, "full"), "sqlite", settings=settings)
    assert full.names() == BUILTIN
    specs = {s.name: s for s in full.specs()}
    assert "${" not in "".join(s.description for s in specs.values())
    assert "read-only sqlite SELECT" in specs["run_sql"].description
    assert "at most 200 rows" in specs["run_sql"].description
    assert specs["final_answer"].parameters["properties"]["status"]["enum"] == [
        "answered",
        "cannot_answer",
    ]
    baseline = api.build_toolset(load_profile(settings, "baseline"), "sqlite", settings=settings)
    assert baseline.names() == ["run_sql", "final_answer"]


def test_extra_tools_can_be_added(repo_root):
    settings = load_settings(repo_root)
    extra = TOOLS.get("list_tables")("benchmark-supplied tool")
    profile = load_profile(settings, "baseline")
    ts = api.build_toolset(profile, "sqlite", settings=settings, extra_tools=[extra])
    assert "list_tables" in ts
