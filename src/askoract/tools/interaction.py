"""Tools that talk to the user or end the run."""

from pydantic import BaseModel, Field

from askoract.agent.types import FinalAnswer
from askoract.db.base import QueryError
from askoract.tools.base import Tool, ToolContext, ToolResult, register_tool

# Shown once when an answer arrives with an assumption and nothing was asked yet.
ASSUMPTION_CHECK = (
    'Not recorded yet. Check your assumption first: "{assumption}". Does it choose between '
    "readings the person asking could reasonably mean (a definition, which records count, "
    "the period, or the measure) that give different results? If so, that choice is theirs: "
    "call ask_user. If it only covers technical details, or the question itself states it, "
    "call final_answer again with the same answer."
)


class AskArgs(BaseModel):
    question: str = Field(description="One clarifying question, in business language.")


@register_tool
class AskUser(Tool):
    name = "ask_user"
    input_model = AskArgs

    def run(self, args: AskArgs, ctx: ToolContext) -> ToolResult:
        limit = ctx.limits.max_clarifications
        if len(ctx.clarifications) >= limit:
            return ToolResult.error(
                f"You have already asked {limit} clarifying question(s). Do not ask again: "
                "proceed with the most reasonable interpretation and state it as your assumption."
            )
        reply = ctx.user.reply(args.question)
        ctx.clarifications.append((args.question, reply))
        return ToolResult(
            content=f"User replied: {reply}", data={"question": args.question, "reply": reply}
        )


@register_tool
class SubmitFinalAnswer(Tool):
    name = "final_answer"
    input_model = FinalAnswer
    terminal = True

    def run(self, args: FinalAnswer, ctx: ToolContext) -> ToolResult:
        if args.status == "answered":
            if not (args.sql and args.sql.strip()):
                return ToolResult.error("sql is required when status is 'answered'.")
            # The scorer re-runs this SQL, so it must run. Better to catch it here.
            try:
                ctx.db.execute(args.sql, max_rows=1, timeout_s=ctx.limits.sql_timeout_s)
            except QueryError as exc:
                return ToolResult.error(f"the sql in final_answer does not run: {exc}")
        if (
            ctx.assumption_check
            and args.status == "answered"
            and args.assumption
            and not ctx.clarifications
            and not ctx.assumption_reviewed
        ):
            ctx.assumption_reviewed = True
            return ToolResult(
                content=ASSUMPTION_CHECK.format(assumption=args.assumption),
                data={"assumption_check": args.assumption},
            )
        return ToolResult(content="Final answer recorded.", data=args.model_dump(), final=args)
