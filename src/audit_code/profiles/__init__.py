"""Project-specific audit profiles — opt-in via --profile."""

from audit_code.profiles.agent_engine.profile import run as _run_ae


def load(name: str):
    """Return the run() function for a named profile, or None."""
    if name == "agent-engine":
        return _run_ae
    return None
