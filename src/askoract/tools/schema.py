"""Schema exploration tools: what tables exist, what they hold, what values look like.

These only use the `Database` protocol, so they work on any engine.
"""

import difflib

from pydantic import BaseModel, Field

from askoract.db.base import ColumnInfo, QueryError, TableSchema, quote_ident
from askoract.tools.base import Tool, ToolContext, ToolResult, register_tool
from askoract.tools.formatting import clip, format_table, format_value, json_safe

NUMERIC_TYPES = ("INT", "REAL", "NUM", "DEC", "FLOAT", "DOUBLE")


def _find_column(schema: TableSchema, column: str) -> ColumnInfo:
    wanted = column.strip().strip('[]"`')
    for col in schema.columns:
        if col.name.lower() == wanted.lower():
            return col
    names = [c.name for c in schema.columns]
    close = difflib.get_close_matches(wanted, names, n=3, cutoff=0.5)
    hint = f" Did you mean: {', '.join(close)}?" if close else f" Columns: {', '.join(names)}."
    raise QueryError(f"no column {column!r} in {schema.name}.{hint}")


class NoArgs(BaseModel):
    pass


@register_tool
class ListTables(Tool):
    name = "list_tables"
    input_model = NoArgs

    def run(self, args: NoArgs, ctx: ToolContext) -> ToolResult:
        tables = ctx.db.list_tables()
        n_tables = sum(t.kind == "table" for t in tables)
        lines = [f"{n_tables} tables, {len(tables) - n_tables} views:"]
        for t in tables:
            count = f"{t.row_count:,} rows" if t.row_count is not None else ""
            lines.append(f"  {t.kind:<5}  {t.name:<34} {count}".rstrip())
        return ToolResult(
            content="\n".join(lines),
            data={
                "tables": [{"name": t.name, "kind": t.kind, "rows": t.row_count} for t in tables]
            },
        )


class TableArgs(BaseModel):
    table: str = Field(description="Table or view name, exactly as listed by list_tables.")


@register_tool
class DescribeTable(Tool):
    name = "describe_table"
    input_model = TableArgs

    def run(self, args: TableArgs, ctx: ToolContext) -> ToolResult:
        try:
            schema = ctx.db.describe(args.table)
        except QueryError as exc:
            return ToolResult.error(str(exc))
        fk_by_col = {fk.column: fk for fk in schema.foreign_keys}
        header = f"{schema.name}" + (f"  -- {schema.comment}" if schema.comment else "")
        lines = [header, "columns:"]
        width = max((len(c.name) for c in schema.columns), default=0)
        for c in schema.columns:
            parts = [f"  {c.name:<{width}}  {c.type or '?':<9}"]
            if c.primary_key:
                parts.append("PK")
            if not c.nullable:
                parts.append("NOT NULL")
            if c.default is not None:
                parts.append(f"default {c.default}")
            if fk := fk_by_col.get(c.name):
                parts.append(f"-> {fk.ref_table}.{fk.ref_column}")
            if c.comment:
                parts.append(f"-- {c.comment}")
            lines.append(" ".join(parts))
        return ToolResult(
            content="\n".join(lines),
            data={
                "table": schema.name,
                "columns": [c.name for c in schema.columns],
                "foreign_keys": [fk.__dict__ for fk in schema.foreign_keys],
            },
        )


class SampleArgs(BaseModel):
    table: str = Field(description="Table or view name.")
    n: int = Field(default=5, ge=1, le=20, description="Number of rows (1-20).")


@register_tool
class SampleRows(Tool):
    name = "sample_rows"
    input_model = SampleArgs

    def run(self, args: SampleArgs, ctx: ToolContext) -> ToolResult:
        limits = ctx.limits
        try:
            name = ctx.db.describe(args.table).name
            q = quote_ident(name)
            total = ctx.db.execute(
                f"SELECT COUNT(*) FROM {q}", max_rows=1, timeout_s=limits.sql_timeout_s
            ).rows[0][0]
            # Rows spread evenly through the table, so early-row quirks don't hide the rest.
            offsets = sorted({(i * total) // args.n for i in range(args.n)}) if total else []
            columns: list[str] = []
            rows = []
            for offset in offsets:
                res = ctx.db.execute(
                    f"SELECT * FROM {q} LIMIT 1 OFFSET {offset}",
                    max_rows=1,
                    timeout_s=limits.sql_timeout_s,
                )
                columns = res.columns
                rows.extend(res.rows)
        except QueryError as exc:
            return ToolResult.error(str(exc))
        if not rows:
            return ToolResult(content=f"{name} is empty.", data={"table": name, "rows": []})
        body = format_table(columns, rows, limits.max_output_chars)
        return ToolResult(
            content=f"{len(rows)} of {total:,} rows from {name}, spread across the table:\n{body}",
            data={
                "table": name,
                "columns": columns,
                "rows": [[json_safe(v) for v in r] for r in rows],
            },
        )


class ColumnArgs(BaseModel):
    table: str = Field(description="Table or view name.")
    column: str = Field(description="Column name.")
    top_k: int = Field(default=10, ge=1, le=50, description="How many frequent values to show.")


@register_tool
class GetColumnStats(Tool):
    name = "get_column_stats"
    input_model = ColumnArgs

    def run(self, args: ColumnArgs, ctx: ToolContext) -> ToolResult:
        timeout = ctx.limits.sql_timeout_s
        try:
            schema = ctx.db.describe(args.table)
            col = _find_column(schema, args.column)
            t, c = quote_ident(schema.name), quote_ident(col.name)
            numeric = any(k in col.type.upper() for k in NUMERIC_TYPES)
            avg = f", AVG({c})" if numeric else ""
            stats = ctx.db.execute(
                f"SELECT COUNT(*), COUNT({c}), COUNT(DISTINCT {c}), MIN({c}), MAX({c}){avg} "
                f"FROM {t}",
                max_rows=1,
                timeout_s=timeout,
            ).rows[0]
            top = ctx.db.execute(
                f"SELECT {c}, COUNT(*) AS n FROM {t} GROUP BY {c} ORDER BY n DESC, {c} "
                f"LIMIT {args.top_k}",
                max_rows=args.top_k,
                timeout_s=timeout,
            ).rows
        except QueryError as exc:
            return ToolResult.error(str(exc))

        rows, non_null, distinct, lo, hi = stats[:5]
        lines = [
            f"{schema.name}.{col.name}  ({col.type or 'untyped'})"
            + (f"  -- {col.comment}" if col.comment else ""),
            f"  rows: {rows:,}   nulls: {rows - non_null:,}   distinct: {distinct:,}",
            f"  min: {format_value(lo)}   max: {format_value(hi)}"
            + (f"   avg: {format_value(stats[5])}" if numeric else ""),
            f"  top {len(top)} values (value: count):",
            *[f"    {format_value(v)}: {n:,}" for v, n in top],
        ]
        if distinct > len(top):
            lines.append(f"    ... {distinct - len(top):,} more distinct values")
        return ToolResult(
            content=clip("\n".join(lines), ctx.limits.max_output_chars),
            data={
                "table": schema.name,
                "column": col.name,
                "rows": rows,
                "nulls": rows - non_null,
                "distinct": distinct,
                "min": json_safe(lo),
                "max": json_safe(hi),
                "top": [[json_safe(v), n] for v, n in top],
            },
        )
