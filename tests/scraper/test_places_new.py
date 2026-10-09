"""Tests for the Places API (New) -> legacy-shape adapter (mocked HTTP)."""
from unittest.mock import patch, MagicMock

import pytest

from scraper.utils import places_new as pn


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


def test_details_field_mapping():
    payload = {"displayName": {"text": "A"}, "nationalPhoneNumber": "(620) 241-8800",
               "websiteUri": "http://a.com/"}
    with patch("requests.get", return_value=_resp(200, payload)) as get:
        out = pn.place_details("k", "pid", fields="name,formatted_phone_number,website")
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
