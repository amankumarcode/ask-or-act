# Prompts

Every piece of text the models see lives here, versioned by filename. Nothing is written inline in code.

| Path | Used for | Referenced from |
|---|---|---|
| `agent/<name>_vN.md` | agent system prompt | `prompt:` in `configs/agents/*.yaml` |
| `tools/vN.yaml` | one description per tool name | `tool_descriptions:` in a profile |
| `sim_user/vN.md` | simulated-user system prompt | `simulated_user.prompt` in `configs/default.yaml` |

Never edit a version that a published result used. Copy it to `_v(N+1)` and point a profile at the new file. Every run records the versions it used in `runs/<id>/manifest.json`.
