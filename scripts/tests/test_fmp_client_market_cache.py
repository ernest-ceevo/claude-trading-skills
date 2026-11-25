"""Daily-bar market cache tests (MARKET_CACHE_PATH read-through + gap fill)."""

from __future__ import annotations

import importlib
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load(rel_path):
    module_path = _ROOT / rel_path
    spec = importlib.util.spec_from_file_location(
        f"mc_{module_path.parent.parent.name}", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_bars(module, count=15):
    """Synthetic weekday bars ending at the expected latest completed session."""
    expected = module._cache_expected_latest_date()
    assert expected, "expected-latest-date helper must resolve"
    year, month, day = (int(part) for part in expected.split("-"))
    d = date(year, month, day)
    dates = []
    while len(dates) < count:
        if d.weekday() < 5:
            dates.append(d)
        d = d - timedelta(days=1)
    dates.reverse()
    close = 100.0
    bars = []
    for dt in dates:
        close += 1.0
        bars.append(
            {
                "t": dt.isoformat() + "T05:00:00Z",
                "o": round(close - 1.0, 2),
                "h": round(close + 0.5, 2),
                "l": round(close - 1.5, 2),
                "c": round(close, 2),
                "v": 12345,
                "n": 100,
                "vw": round(close - 0.2, 2),
            }
        )
    return bars


@pytest.fixture()
def vcp_client(tmp_path, monkeypatch):
    """Rendered vcp-screener client with a mocked Alpaca transport + cache."""
    monkeypatch.setenv("DATA_SOURCE", "alpaca")
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")  # pragma: allowlist secret
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")  # pragma: allowlist secret
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    db_path = tmp_path / "bars.db"
    monkeypatch.setenv("MARKET_CACHE_PATH", str(db_path))
    mod = _load("skills/vcp-screener/scripts/fmp_client.py")
    client = mod.FMPClient()
    state = {"calls": 0, "db": db_path}

    def fake_alpaca_get(path, params):
        state["calls"] += 1
        client.api_calls_made += 1  # mirror the real transport's budget accounting
        requested = (params.get("symbols") or "").split(",")
        out = {}
        for sym in requested:
            out[sym] = _make_bars(mod)
        return {"bars": out}

    monkeypatch.setattr(client, "_alpaca_get", fake_alpaca_get)
    return client, state


def test_cache_hit_serves_second_call_without_network(vcp_client):
    client, state = vcp_client
    first = client.get_historical_prices("VCPX", 10)
    assert state["calls"] == 1
    assert first["symbol"] == "VCPX"
    assert len(first["historical"]) == 10
    assert state["db"].exists()

    second = client.get_historical_prices("VCPX", 10)
    assert state["calls"] == 1  # second call never touched the transport
    assert [r["date"] for r in second["historical"]] == [r["date"] for r in first["historical"]]
    assert [r["close"] for r in second["historical"]] == [r["close"] for r in first["historical"]]
    stats = client.get_api_stats()
    assert stats["api_calls_made"] == 1  # provider budget untouched by the hit


def test_gap_fill_fetches_only_missing_symbols_and_caches(vcp_client):
    client, state = vcp_client
    client.get_historical_prices("AAA", 10)
    assert state["calls"] == 1
    result = client.get_historical_prices("BBB", 10)
    assert state["calls"] == 2  # only the missing symbol reached the transport
    assert result["symbol"] == "BBB"
    assert len(result["historical"]) == 10


def test_multi_symbol_request_served_from_seeded_cache(vcp_client):
    client, state = vcp_client
    client.get_historical_prices("AAA", 10)
    client.get_historical_prices("BBB", 10)
    batch = client.get_batch_historical(["AAA", "BBB"], 10)
    assert state["calls"] == 2  # batch resolved entirely from the cache
    assert set(batch) == {"AAA", "BBB"}
    assert all(len(rows) == 10 for rows in batch.values())


def test_cache_failure_fails_open_to_network(tmp_path, monkeypatch, vcp_client):
    client, state = vcp_client
    monkeypatch.setenv("MARKET_CACHE_PATH", "/proc/definitely/not/writable/x.db")
    data = client.get_historical_prices("CCC", 5)
    assert state["calls"] == 1
    assert data is not None
    assert len(data["historical"]) == 5
