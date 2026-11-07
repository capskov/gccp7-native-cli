from dataclasses import dataclass

from .contracts import TOOLS


@dataclass(frozen=True)
class Capability:
    name: str
    state: str = "unavailable"
    available: bool = False
    limitations: tuple[str, ...] = ("M01 contract only; runtime adapter not connected",)


def default_capabilities() -> tuple[Capability, ...]:
    """No mutation/read tool is advertised as operational by contract validation."""
    return tuple(Capability(name) for name in TOOLS)
