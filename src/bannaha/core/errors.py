class BannahaError(Exception):
    """Base class for bannaha errors."""


class FleetError(BannahaError):
    """The fleet spec is malformed or cannot run in this environment."""


class TurnControl(BannahaError):
    """Raised from inside a tool call to end the current agent turn early."""


class AgentDropped(TurnControl):
    """The agent was removed from the fleet (fault injection)."""


class TurnLimitReached(TurnControl):
    """The agent used its tool-call allowance for this turn."""


class SessionStopped(TurnControl):
    """The session was cancelled or ran out of wall time."""
