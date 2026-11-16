"""Transient JSON gateway errors may recover without extending the original timeout."""
from types import SimpleNamespace

import httpx
import pytest

from k12_server import ai


@pytest.fixture
def provider(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ai.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ai.time, "sleep", lambda value: clock.__setitem__(0, clock[0] + value))
    monkeypatch.setattr(ai, "_model_unavailable_until", {})
    monkeypatch.setattr(ai, "_usage_family", SimpleNamespace(get=lambda: None))
    settings = SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="private", ai_timeout_seconds=10)
    calls = []

    def setup(responses, elapsed=2):
        def post(url, **kwargs):
            calls.append(kwargs)
            clock[0] += elapsed
            status, payload = responses[min(len(calls) - 1, len(responses) - 1)]
            return httpx.Response(status, json=payload, request=httpx.Request("POST", url))
        monkeypatch.setattr(ai.httpx, "post", post)
    return settings, calls, clock, setup


def test_gateway_rejection_retries_same_request_once_with_remaining_time(provider):
    settings, calls, clock, setup = provider
    setup([(502, {"error": "gateway"}), (200, {"model": "gpt-6-astra", "answer": "ok"})])
    result = ai.post_model(settings, {"model": "gpt-6-astra", "messages": ["unchanged"]})
    assert result["answer"] == "ok" and len(calls) == 2
    assert calls[0]["json"] == calls[1]["json"]
    assert calls[0]["timeout"] == 10 and calls[1]["timeout"] == 7.75
    assert clock[0] < 110


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_nontransient_errors_never_retry(provider, status):
    settings, calls, _, setup = provider
    setup([(status, {"error": "not a gateway rejection"})])
    with pytest.raises(httpx.HTTPStatusError):
        ai.post_model(settings, {"model": "gpt-6-astra"})
    assert len(calls) == 1


@pytest.mark.parametrize("code", ["insufficient_quota", "billing_hard_limit_reached", "invalid_api_key", "model_not_found"])
def test_business_errors_mapped_to_503_do_not_retry(provider, code):
    settings, calls, _, setup = provider
    setup([(503, {"error": {"code": code}})])
    with pytest.raises(httpx.HTTPStatusError):
        ai.post_model(settings, {"model": "gpt-6-astra"})
    assert len(calls) == 1


def test_explicit_model_unavailability_keeps_existing_fallback_semantics(provider):
    settings, calls, _, setup = provider
    setup([(503, {"error": {"code": "model_not_found"}}), (200, {"model": "gpt-6-astra"})])
    ai.post_model(settings, {"model": "gpt-6.1-sol"})
    assert [call["json"]["model"] for call in calls] == ["gpt-6.1-sol", "gpt-6-astra"]


def test_gateway_failures_are_bounded_and_do_not_restart_the_timeout(provider):
    settings, calls, clock, setup = provider
    setup([(502, {"error": "gateway"})], elapsed=6)
    with pytest.raises(httpx.HTTPStatusError):
        ai.post_model(settings, {"model": "gpt-6-astra"})
    assert len(calls) == 2 and calls[1]["timeout"] < calls[0]["timeout"]
    # The fake provider can overrun a timeout; the real HTTP client is given
    # only the remaining deadline, rather than another full ten seconds.


def test_no_retry_when_the_original_deadline_is_already_exhausted(provider):
    settings, calls, _, setup = provider
    setup([(502, {"error": "gateway"})], elapsed=10)
    with pytest.raises(httpx.HTTPStatusError):
        ai.post_model(settings, {"model": "gpt-6-astra"})
    assert len(calls) == 1
