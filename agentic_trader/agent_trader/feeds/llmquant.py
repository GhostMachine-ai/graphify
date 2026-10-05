"""LLMQuant Data HTTP feed.

**Unverified against the live service.** ``LLMQUANT_API_KEY`` is not set in the
environment this was built in, so this adapter is exercised only against an
injected fake transport in the tests. The request shape follows the uploaded
``@llmquant/data-mcp`` package's configuration (``LLMQUANT_BASE_URL`` defaulting to
``https://api.llmquantdata.com``), but the response parsing is a best-effort
mapping over several plausible field namings and should be confirmed against the
real API before anyone trusts a number that came through it.

``urllib`` is used rather than ``requests`` because this package is
standard-library only -- see ``agent_trader.indicators`` for why.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from agent_trader.feeds.base import FeedError
from agent_trader.feeds.cached import bars_from_payload
from agent_trader.types import Bar

DEFAULT_BASE_URL = "https://api.llmquantdata.com"

#: Field aliases seen across market-data APIs, tried in order.
_ALIASES = {
    "ts": ("ts", "timestamp", "time", "date", "datetime", "begins_at"),
    "open": ("open", "o", "open_price"),
    "high": ("high", "h", "high_price"),
    "low": ("low", "l", "low_price"),
    "close": ("close", "c", "close_price", "last", "adj_close"),
    "volume": ("volume", "v", "vol"),
}

Transport = Callable[[str, dict[str, str]], str]


def _http_get(url: str, headers: dict[str, str]) -> str:
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 - https only, checked
    if not url.lower().startswith("https://"):
        raise FeedError(f"refusing non-HTTPS request to {url!r}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise FeedError(f"LLMQuant HTTP {exc.code}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FeedError(f"LLMQuant request failed: {exc}") from exc


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map one API row onto the cache bar schema, trying known field aliases."""
    out: dict[str, Any] = {}
    for canonical, names in _ALIASES.items():
        for n in names:
            if n in row and row[n] is not None:
                out[canonical] = row[n]
                break
        if canonical not in out:
            if canonical == "volume":
                out["volume"] = 0.0  # some endpoints omit volume entirely
            else:
                raise FeedError(
                    f"row missing a usable {canonical!r} field; saw keys {sorted(row)}"
                )
    return out


class LLMQuantFeed:
    """Fetch OHLCV from LLMQuant Data.

    Pass ``transport`` to inject a fake for tests; the default performs real HTTPS.
    """

    is_synthetic = False

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("LLMQUANT_API_KEY")
        self.base_url = (
            base_url or os.environ.get("LLMQUANT_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._transport = transport or _http_get

    @property
    def configured(self) -> bool:
        """Whether an API key is present. False in this build environment."""
        return bool(self.api_key)

    def load(self, symbol: str, interval: str) -> list[Bar]:
        if not self.configured:
            raise FeedError(
                "LLMQUANT_API_KEY is not set, so the LLMQuant feed cannot be used. "
                "Set it in the environment, or use CachedFeed with bars refreshed "
                "through the agent's MCP tools (see docs/DATA.md)."
            )
        query = urllib.parse.urlencode({"symbol": symbol, "interval": interval})
        url = f"{self.base_url}/v1/prices/ohlcv?{query}"
        text = self._transport(url, {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FeedError(f"LLMQuant returned non-JSON: {exc}") from exc

        rows = data.get("data") if isinstance(data, dict) else data
        if isinstance(rows, dict):
            rows = rows.get("bars") or rows.get("results")
        if not isinstance(rows, list):
            raise FeedError(
                f"could not find a bar list in the LLMQuant response "
                f"(top-level keys: {sorted(data) if isinstance(data, dict) else type(data)})"
            )
        payload = {
            "symbol": symbol,
            "interval": interval,
            "bars": [normalize_row(r) for r in rows if isinstance(r, dict)],
        }
        return bars_from_payload(payload, symbol)
