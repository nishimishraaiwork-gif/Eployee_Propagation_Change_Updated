"""AI-assisted exception analysis for failed onboarding-system updates.

The application keeps validation, retries, and write decisions deterministic in
Python. Claude is used only to explain exhausted failures and recommend the next
operational step. No employee PII is sent to the model.
"""
from __future__ import annotations

import json
import os
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ExceptionAnalyzer(Protocol):
    def analyze(self, destination: str, error: str, attempts: int) -> dict[str, Any]:
        """Return a short explanation and a safe next-step recommendation."""


class OfflineDemoAnalyzer:
    """Offline stand-in used when no Anthropic API key is configured.

    This is intentionally labelled as a mock; it is not represented as a live
    Claude response. Set ANTHROPIC_API_KEY to use the real Claude API.
    """

    def analyze(self, destination: str, error: str, attempts: int) -> dict[str, Any]:
        return {
            "provider": "offline_demo_mock",
            "issue": f"The {destination} connector failed after {attempts} attempts.",
            "recommendation": "Check connector health and credentials, then retry after the underlying issue is resolved.",
            "requires_human_review": True,
        }


class ClaudeAPIAnalyzer:
    """Minimal Claude Messages API client using Python's standard library."""

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: int = 20):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
        self.timeout = timeout
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required to use ClaudeAPIAnalyzer")

    def analyze(self, destination: str, error: str, attempts: int) -> dict[str, Any]:
        # Keep the prompt deliberately PII-free: only system name and technical error.
        prompt = (
            "You are an operations assistant for an employee-onboarding integration. "
            "A deterministic Python workflow has already exhausted its retries. "
            "Do not claim the update succeeded and do not suggest bypassing controls. "
            "Return ONLY a JSON object with keys: issue (string), recommendation (string), "
            "requires_human_review (boolean). Recommend safe diagnostics or manual review.\n\n"
            f"Destination system: {destination}\n"
            f"Attempts: {attempts}\n"
            f"Technical error: {error[:1200]}"
        )
        payload = {
            "model": self.model,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        }
        request = Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Claude API returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Claude API request failed: {exc}") from exc

        text_blocks = [
            block.get("text", "")
            for block in response_data.get("content", [])
            if block.get("type") == "text"
        ]
        raw_text = "\n".join(text_blocks).strip()
        # Be tolerant of a model accidentally wrapping JSON in a code fence.
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude response was not valid JSON") from exc
        if not isinstance(result, dict) or not all(
            key in result for key in ("issue", "recommendation", "requires_human_review")
        ):
            raise RuntimeError("Claude response is missing required fields")
        if not isinstance(result["requires_human_review"], bool):
            raise RuntimeError("Claude requires_human_review must be a boolean")
        result["provider"] = "claude_api"
        return result


def build_exception_analyzer() -> ExceptionAnalyzer:
    """Use live Claude when configured; otherwise use the clearly labelled mock."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return ClaudeAPIAnalyzer()
    return OfflineDemoAnalyzer()
