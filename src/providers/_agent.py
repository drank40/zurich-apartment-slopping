"""Minimal scaffold around claude-agent-sdk for one-shot LLM extraction.

We only use the ``Agent.run`` method here — no tools, no hooks, no
sub-agents. Kept self-contained so the rest of the package doesn't have
to depend on the SDK unless someone actually uses ``llm_extract``.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    HookMatcher,
    create_sdk_mcp_server,
    query,
)

# Neutral cwd so the spawned Claude CLI doesn't pick up project context
# from the host's working directory and bias its outputs.
_NEUTRAL_CWD = tempfile.mkdtemp(prefix="agent-")


def _load_anthropic_key_from_creds() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    creds = Path(__file__).resolve().parents[2] / ".creds"
    if not creds.exists():
        return
    for raw in creds.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "ANTHROPIC_API_KEY" and value.strip():
            os.environ["ANTHROPIC_API_KEY"] = value.strip().strip("\"'")
            return


@dataclass
class Agent:
    name: str
    system_prompt: str
    tools: list[Callable] = field(default_factory=list)
    allowed_builtins: list[str] = field(default_factory=list)
    hooks: dict[str, list[HookMatcher]] = field(default_factory=dict)
    model: str = "haiku"
    max_turns: int = 4
    cwd: str | None = None
    agents: dict[str, AgentDefinition] = field(default_factory=dict)
    task_budget_tokens: int = 0

    def _build_options(self, **overrides) -> ClaudeAgentOptions:
        _load_anthropic_key_from_creds()
        mcp_servers = {}
        allowed = list(self.allowed_builtins)
        if self.tools:
            srv = create_sdk_mcp_server(name=self.name, version="1.0.0", tools=self.tools)
            mcp_servers[self.name] = srv
            allowed += [f"mcp__{self.name}__{t.name}" for t in self.tools]

        budget = overrides.pop("task_budget_tokens", self.task_budget_tokens)
        task_budget = {"total": int(budget)} if budget else None
        return ClaudeAgentOptions(
            system_prompt=self.system_prompt,
            allowed_tools=allowed,
            mcp_servers=mcp_servers,
            hooks=self.hooks,
            model=self.model,
            max_turns=self.max_turns,
            cwd=overrides.pop("cwd", self.cwd) or _NEUTRAL_CWD,
            permission_mode="bypassPermissions",
            setting_sources=[],
            skills=[],
            agents=self.agents or None,
            task_budget=task_budget,
            **overrides,
        )

    async def run(self, prompt: str, **overrides) -> str:
        out: list[str] = []
        async for msg in query(prompt=prompt, options=self._build_options(**overrides)):
            if hasattr(msg, "result") and msg.result:
                out.append(msg.result)
        return "\n".join(out)

    def __call__(self, prompt: str, **overrides) -> str:
        return asyncio.run(self.run(prompt, **overrides))
