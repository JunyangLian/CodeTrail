"""P1 feature flags for the self-improvement loop.

All flags default OFF. Frozen A/B conditions (promptv1/promptv2, guard
set, gateway parameters) are NOT flags and can never be flipped here - a
strategy change goes through the evolution state machine only.
"""
from __future__ import annotations

FEATURE_FLAGS: dict[str, bool] = {
    # runtime: bounded evidence self-correction (opt-in per run strategy)
    "self_refinement": False,
    # flywheel: user-uploaded data auto-entering the regression pool
    "data_flywheel_auto_ingest": False,
    # evolution: shadow results automatically promoting a candidate
    "shadow_auto_promote": False,
}

# flags that can never be enabled (hard safety rails)
PERMANENTLY_DISABLED = {"shadow_auto_promote"}


def get_flags() -> dict[str, bool]:
    return dict(FEATURE_FLAGS)


def is_enabled(name: str) -> bool:
    if name in PERMANENTLY_DISABLED:
        return False
    return bool(FEATURE_FLAGS.get(name, False))


def set_flag(name: str, value: bool) -> dict[str, bool]:
    if name in PERMANENTLY_DISABLED:
        raise ValueError(f"flag {name} is permanently disabled by policy")
    if name not in FEATURE_FLAGS:
        raise KeyError(f"unknown feature flag: {name}")
    FEATURE_FLAGS[name] = bool(value)
    return get_flags()


SIMULATED_REVIEWER_PREFIXES = ("offline_simulation", "mock", "simulated")


def is_simulated_identity(identity: str) -> bool:
    return (identity or "").startswith(SIMULATED_REVIEWER_PREFIXES)
