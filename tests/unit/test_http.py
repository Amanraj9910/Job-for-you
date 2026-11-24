import httpx
import pytest
import respx

from jobhunt.util import http as H


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(H.time, "sleep", lambda s: slept.append(s))
    # tenacity sleeps through its own hook
    monkeypatch.setattr(H.Http.request.retry, "sleep", lambda s: slept.append(s))
    H._last_hit.clear()
    return slept


@respx.mock
def test_retry_on_429_then_ok():
    route = respx.get("https://api.example.com/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"a": 1})])
    assert H.Http().get_json("https://api.example.com/x") == {"a": 1}
    assert route.call_count == 3


@respx.mock
def test_gives_up_after_three():
    respx.get("https://api.example.com/y").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        H.Http().get("https://api.example.com/y")


@respx.mock
def test_404_returns_none():
    respx.get("https://api.example.com/z").mock(return_value=httpx.Response(404))
    assert H.Http().get_json("https://api.example.com/z") is None


@respx.mock
def test_400_raises_without_retry():
    route = respx.get("https://api.example.com/b").mock(return_value=httpx.Response(400))
    with pytest.raises(httpx.HTTPStatusError):
        H.Http().get("https://api.example.com/b")
    assert route.call_count == 1


def test_throttle_spaces_same_host(monkeypatch, no_sleep):
    clock = [100.0]
    monkeypatch.setattr(H.time, "monotonic", lambda: clock[0])
    H._throttle("https://h.example.com/1")
    H._throttle("https://h.example.com/2")
    assert no_sleep and no_sleep[-1] >= 0.99
    H._throttle("https://other.example.com/1")  # different host: no extra wait
    assert len(no_sleep) == 1


@respx.mock
def test_timeout_is_retried_then_raised():
    respx.get("https://api.example.com/t").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        H.Http().get("https://api.example.com/t")
