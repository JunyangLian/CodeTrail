"""Release-audit hardening (V3.1): global experiment budget guard.

The guard is a PROCESS-GLOBAL ledger with worst-case reservation. On resume
it MUST be seeded from the historical run ledger (``from_ledger``) - a
restarted process that resets the budget accounting violates the frozen
protocol (P2.4: no per-subprocess budget resets).
"""
from __future__ import annotations

import json
from pathlib import Path

BILLING_KINDS = ("observation", "repair")


class BudgetGuard:
    def __init__(self, token_cap: int, request_cap: int):
        self.token_cap = token_cap
        self.request_cap = request_cap
        self.tokens = 0
        self.requests = 0
        self.reserved_tokens = 0
        self.reserved_requests = 0

    @classmethod
    def from_ledger(cls, ledger_path: Path, token_cap: int,
                    request_cap: int) -> BudgetGuard:
        """Seed the guard from every billed line of a run ledger
        (observations AND separately-listed repairs)."""
        guard = cls(token_cap, request_cap)
        if not Path(ledger_path).is_file():
            return guard
        for line in Path(ledger_path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("kind") in BILLING_KINDS:
                guard.tokens += int(rec.get("tokens") or 0)
                guard.requests += int(rec.get("requests") or 0)
        return guard

    def reserve(self, tokens: int, requests: int) -> bool:
        """Reserve worst-case before starting an observation."""
        if (self.tokens + self.reserved_tokens + tokens > self.token_cap
                or self.requests + self.reserved_requests + requests
                > self.request_cap):
            return False
        self.reserved_tokens += tokens
        self.reserved_requests += requests
        return True

    def settle(self, tokens: int, requests: int,
               reserved_tokens: int, reserved_requests: int) -> None:
        """Replace one reservation with actual usage."""
        self.reserved_tokens = max(0, self.reserved_tokens - reserved_tokens)
        self.reserved_requests = max(0, self.reserved_requests
                                     - reserved_requests)
        self.tokens += tokens
        self.requests += requests

    def snapshot(self) -> dict:
        return {"tokens_spent": self.tokens,
                "tokens_reserved": self.reserved_tokens,
                "token_cap": self.token_cap,
                "tokens_remaining_effective": self.token_cap - self.tokens
                - self.reserved_tokens,
                "requests_spent": self.requests,
                "request_cap": self.request_cap}


def ledger_billing(ledger_path: Path) -> tuple[int, int]:
    """Authoritative (tokens, requests) billed by a ledger: observations AND
    repairs both count; a repair never overwrites the original attempt's
    billing."""
    tokens = requests = 0
    if not Path(ledger_path).is_file():
        return 0, 0
    for line in Path(ledger_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("kind") in BILLING_KINDS:
            tokens += int(rec.get("tokens") or 0)
            requests += int(rec.get("requests") or 0)
    return tokens, requests
