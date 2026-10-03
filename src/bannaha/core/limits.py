from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_tool_calls_per_turn: int = 16
    max_steps_per_turn: int = 24
    max_output_tokens: int = 16384  # a cap, not a target: reasoning models spend output tokens on thinking
    max_wall_s: float = 900.0
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None  # whole run, at list prices (see core.pricing)
    max_agent_cost_usd: float | None = None  # per agent, across all its turns

    def describe(self) -> dict:
        return {"tool_calls_per_turn": self.max_tool_calls_per_turn, "steps_per_turn": self.max_steps_per_turn,
                "output_tokens": self.max_output_tokens, "wall_s": self.max_wall_s,
                "total_tokens": self.max_total_tokens, "cost_usd": self.max_cost_usd,
                "agent_cost_usd": self.max_agent_cost_usd}
