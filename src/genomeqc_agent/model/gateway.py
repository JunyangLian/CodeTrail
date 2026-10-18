"""Model gateway (G4): OpenAI-compatible chat completions via httpx.

Only the planner touches the model; tools never do. Every call records
exact model id, latency, token usage and raw errors - receipts over vibes.
Credentials come exclusively from the environment (.env is git-ignored).
"""
from __future__ import annotations

import time

import httpx


class GatewayError(RuntimeError):
    """Raised for transport/auth/provider failures (NOT for model content)."""


class ChatGateway:
    def __init__(self, api_key: str, api_base: str, model_id: str,
                 timeout: float = 60.0, transport: httpx.BaseTransport | None = None):
        if not api_key or not api_base:
            raise GatewayError("gateway requires GENOMEQC_API_KEY and GENOMEQC_API_BASE")
        self.model_id = model_id
        self.api_base = api_base.rstrip("/")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            timeout=timeout, transport=transport)

    def chat_json(self, system: str, user: str, temperature: float = 0.0,
                  max_tokens: int = 4096) -> dict:
        """One chat completion; returns {'content', 'usage', 'latency_ms',
        'finish_reason', 'reasoning_chars'}.

        GLM-5.3-Flash always emits reasoning tokens that COUNT against
        max_tokens (probed 2026-10-17: enable_thinking=false is ignored);
        a truncated reply is untrustworthy and becomes GatewayError.
        """
        payload = {
            "model": self.model_id,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        started = time.monotonic()
        try:
            response = self._client.post(f"{self.api_base}/chat/completions",
                                         json=payload)
        except httpx.HTTPError as error:
            raise GatewayError(f"transport failure: {type(error).__name__}: {error}") from error
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            raise GatewayError(f"provider returned {response.status_code}: "
                               f"{response.text[:300]}")
        data = response.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            reasoning = choice["message"].get("reasoning_content") or ""
            finish_reason = choice.get("finish_reason")
            usage = data.get("usage", {})
        except (KeyError, IndexError, TypeError) as error:
            raise GatewayError(f"unexpected provider payload: {error}") from error
        if finish_reason == "length":
            raise GatewayError(
                f"output truncated (finish_reason=length, "
                f"completion_tokens={usage.get('completion_tokens')}); "
                f"increase max_tokens")
        return {"content": content, "latency_ms": latency_ms, "usage": usage,
                "finish_reason": finish_reason,
                "reasoning_chars": len(reasoning)}

    def close(self) -> None:
        self._client.close()
