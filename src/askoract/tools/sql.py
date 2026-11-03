import re

from pydantic import BaseModel, Field

from askoract.db.base import QueryError
from askoract.tools.base import Tool, ToolContext, ToolResult, register_tool
from askoract.tools.formatting import format_table, json_safe


def _quoting_hint(sql: str, ctx: ToolContext) -> str:
    """Point out table names with spaces that appear unquoted, a common cause of syntax errors."""
    try:
        spaced = [t.name for t in ctx.db.list_tables() if " " in t.name]
    except QueryError:
        return ""
    for name in spaced:
        pattern = rf'(?<!["\[`]){re.escape(name)}(?!["\]`])'
        if re.search(pattern, sql, flags=re.IGNORECASE):
            return f' Hint: names containing spaces must be double-quoted, e.g. "{name}".'
    return ""


class SqlArgs(BaseModel):
    sql: str = Field(description="One read-only SELECT (or WITH ... SELECT) statement.")


@register_tool
class RunSql(Tool):
    name = "run_sql"
    input_model = SqlArgs

    def run(self, args: SqlArgs, ctx: ToolContext) -> ToolResult:
        limits = ctx.limits
        try:
            res = ctx.db.execute(
                args.sql, max_rows=limits.sql_max_rows, timeout_s=limits.sql_timeout_s
            )
        except QueryError as exc:
            return ToolResult.error(str(exc) + _quoting_hint(args.sql, ctx))

        if not res.columns:
            body = "(statement returned no result set)"
        elif not res.rows:
            body = " | ".join(res.columns) + "\n(0 rows)"
        else:
            body = format_table(res.columns, res.rows, limits.max_output_chars)
        if res.truncated:
            note = (
                f"\n(showing the first {limits.sql_max_rows} rows; more exist. "
                "Aggregate, filter, or add LIMIT.)"
            )
        else:
            note = f"\n({len(res.rows)} row{'s' if len(res.rows) != 1 else ''})"
        return ToolResult(
            content=body + note,
            data={
                "columns": res.columns,
                "rows": [[json_safe(v) for v in r] for r in res.rows],
                "row_count": len(res.rows),
                "truncated": res.truncated,
                "elapsed_ms": round(res.elapsed_ms, 1),
            },
        )
