"""Tests for the Places API (New) -> legacy-shape adapter (mocked HTTP)."""
from unittest.mock import patch, MagicMock

import pytest

from scraper.utils import places_new as pn


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pn, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pn, "MAX_REQUESTS", 400)
    pn._PLACE_MEMO.clear()
    for k in pn.STATS:
        pn.STATS[k] = 0


def _resp(code, payload):
    r = MagicMock()
    r.status_code = code
    r.json.return_value = payload
    r.text = str(payload)
    return r


def test_text_search_translates_to_legacy_shape():
    payload = {"places": [{"id": "abc", "displayName": {"text": "Foo Law"},
                           "formattedAddress": "1 Main St, McPherson, KS 67460, USA",
                           "location": {"latitude": 1.5, "longitude": 2.5}}],
               "nextPageToken": "tok"}
    with patch("requests.post", return_value=_resp(200, payload)):
        out = pn.text_search("k", query="law firm")
    assert out["status"] == "OK"
    assert out["next_page_token"] == "tok"
    assert out["results"][0] == {"place_id": "abc", "name": "Foo Law",
                                 "formatted_address": "1 Main St, McPherson, KS 67460, USA",
                                 "geometry": {"location": {"lat": 1.5, "lng": 2.5}}}


def test_zero_results():
    with patch("requests.post", return_value=_resp(200, {})):
        assert pn.text_search("k", query="x")["status"] == "ZERO_RESULTS"
        assert pn.find_place("k", "x")["status"] == "ZERO_RESULTS"


def test_bad_key_and_denied_map_to_request_denied():
    bad = _resp(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
    denied = _resp(403, {"error": {"message": "nope"}})
    with patch("requests.post", return_value=bad):
        assert pn.text_search("k", query="x")["status"] == "REQUEST_DENIED"
    with patch("requests.post", return_value=denied):
        assert pn.find_place("k", "x")["status"] == "REQUEST_DENIED"


def test_client_remembers_query_for_pagination_and_raises_on_error():
    c = pn.Client("k")
    ok = _resp(200, {"places": [], "nextPageToken": "t"})
    with patch("requests.post", return_value=ok) as post:
        c.places(query="law firm in X")
        c.places(page_token="t")
    assert post.call_args.kwargs["json"]["textQuery"] == "law firm in X"
    assert post.call_args.kwargs["json"]["pageToken"] == "t"
    with patch("requests.post", return_value=_resp(403, {"error": {"message": "x"}})):
        with pytest.raises(RuntimeError):
            c.places(query="y")


def test_details_served_from_search_without_extra_call():
    payload = {"places": [{"id": "p1", "displayName": {"text": "A"},
                           "websiteUri": "http://a.com/"}]}
    with patch("requests.post", return_value=_resp(200, payload)), \
         patch("requests.get") as get:
        pn.text_search("k", query="q")
        out = pn.place_details("k", "p1", fields=["name", "website", "formatted_phone_number"])
    get.assert_not_called()
    assert out["result"]["website"] == "http://a.com/"
    assert pn.STATS["live_details"] == 0 and pn.STATS["memo_hits"] == 1


def test_repeat_search_hits_disk_cache_not_api():
    payload = {"places": [{"id": "p1", "displayName": {"text": "A"}}]}
    with patch("requests.post", return_value=_resp(200, payload)) as post:
        pn.text_search("k", query="q")
        pn.text_search("k", query="q")
    assert post.call_count == 1 and pn.STATS["cache_hits"] == 1


def test_errors_not_cached():
    with patch("requests.post", return_value=_resp(403, {"error": {"message": "x"}})) as post:
        pn.text_search("k", query="q")
        pn.text_search("k", query="q")
    assert post.call_count == 2


def test_request_cap_raises(monkeypatch):
    monkeypatch.setattr(pn, "MAX_REQUESTS", 2)
    with patch("requests.post", return_value=_resp(200, {})):
        pn.text_search("k", query="a")
        pn.text_search("k", query="b")
        with pytest.raises(pn.BudgetExceeded):
            pn.text_search("k", query="c")


def test_details_field_mapping():
    payload = {"displayName": {"text": "A"}, "nationalPhoneNumber": "(620) 241-8800",
               "websiteUri": "http://a.com/"}
    with patch("requests.get", return_value=_resp(200, payload)) as get:
        out = pn.place_details("k", "unseen-pid", fields="name,formatted_phone_number,website")
    assert out["result"] == {"name": "A", "formatted_phone_number": "(620) 241-8800",
                             "website": "http://a.com/"}
    mask = get.call_args.kwargs["headers"]["X-Goog-FieldMask"]
    assert set(mask.split(",")) == {"displayName", "nationalPhoneNumber", "websiteUri"}


def test_legacy_get_dispatch():
    with patch("requests.post", return_value=_resp(200, {})):
        r = pn.legacy_get("https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
                          {"input": "x", "key": "k"})
    assert r.json()["status"] == "ZERO_RESULTS"
    with pytest.raises(ValueError):
        pn.legacy_get("https://example.com/foo", {})
