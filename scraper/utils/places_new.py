"""Google Places API (New) adapter that returns legacy-shaped responses.

The project's Google account only has the *new* Places API enabled
(https://places.googleapis.com/v1). The pipeline code was written against the
legacy Places API / ``googlemaps`` client. This module keeps those call sites
working by translating to the new API and returning the old response shape
(``status``, ``results``, ``result``, ``candidates``, ``next_page_token`` ...).

Public surface:
  * ``Client(key)``  - drop-in for ``googlemaps.Client`` for ``.places()`` and ``.place()``
  * ``legacy_get(url, params, timeout)`` - drop-in for ``requests.get`` against the
    legacy ``textsearch`` / ``findplacefromtext`` / ``details`` URLs; returns an object
    with ``.json()``.

Status mapping: HTTP 401/403 -> REQUEST_DENIED, 429 -> OVER_QUERY_LIMIT,
400 -> INVALID_REQUEST, 404 -> NOT_FOUND, no hits -> ZERO_RESULTS, else OK.
"""
from typing import Optional

import requests

BASE = "https://places.googleapis.com/v1"

_SEARCH_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.businessStatus,places.types,nextPageToken"
)

# legacy field name -> (new field mask entry)
_DETAIL_FIELD_MAP = {
    "name": "displayName",
    "formatted_address": "formattedAddress",
    "formatted_phone_number": "nationalPhoneNumber",
    "website": "websiteUri",
    "geometry": "location",
    "business_status": "businessStatus",
    "type": "types",
    "types": "types",
    "place_id": "id",
}
_DEFAULT_DETAIL_FIELDS = ["name", "formatted_address", "formatted_phone_number",
                          "website", "geometry"]


def _status_for_http(code: int) -> str:
    if code in (401, 403):
        return "REQUEST_DENIED"
    if code == 429:
        return "OVER_QUERY_LIMIT"
    if code == 404:
        return "NOT_FOUND"
    if code == 400:
        return "INVALID_REQUEST"
    return "UNKNOWN_ERROR"


def _error(resp) -> dict:
    try:
        msg = resp.json().get("error", {}).get("message", "")
    except ValueError:
        msg = resp.text[:200]
    status = _status_for_http(resp.status_code)
    if status == "INVALID_REQUEST" and "api key" in msg.lower():
        status = "REQUEST_DENIED"  # bad/expired key: callers treat this as fatal
    return {"status": status, "error_message": msg, "results": []}


def _to_legacy_place(p: dict) -> dict:
    """Convert one New-API place object to the legacy result shape."""
    out = {}
    if "id" in p:
        out["place_id"] = p["id"]
    if "displayName" in p:
        out["name"] = p["displayName"].get("text", "")
    if "formattedAddress" in p:
        out["formatted_address"] = p["formattedAddress"]
    if "nationalPhoneNumber" in p:
        out["formatted_phone_number"] = p["nationalPhoneNumber"]
    if "websiteUri" in p:
        out["website"] = p["websiteUri"]
    if "location" in p:
        out["geometry"] = {"location": {"lat": p["location"].get("latitude"),
                                        "lng": p["location"].get("longitude")}}
    if "businessStatus" in p:
        out["business_status"] = p["businessStatus"]
    if "types" in p:
        out["types"] = p["types"]
    return out


def text_search(api_key: str, query: Optional[str] = None, page_token: Optional[str] = None,
                timeout: int = 15, page_size: int = 20) -> dict:
    """Text Search (New). Returns legacy-shaped dict."""
    body = {"pageSize": page_size}
    if page_token:
        body["pageToken"] = page_token
        if query:
            body["textQuery"] = query
    else:
        body["textQuery"] = query
    resp = requests.post(
        f"{BASE}/places:searchText", json=body, timeout=timeout,
        headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": _SEARCH_MASK},
    )
    if resp.status_code != 200:
        return _error(resp)
    data = resp.json()
    places = [_to_legacy_place(p) for p in data.get("places", [])]
    out = {"status": "OK" if places else "ZERO_RESULTS", "results": places}
    if data.get("nextPageToken"):
        out["next_page_token"] = data["nextPageToken"]
    return out


def place_details(api_key: str, place_id: str, fields=None, timeout: int = 15) -> dict:
    """Place Details (New). Returns {"status", "result"} like the legacy API."""
    if isinstance(fields, str):
        fields = [f.strip() for f in fields.split(",") if f.strip()]
    fields = fields or _DEFAULT_DETAIL_FIELDS
    mask = sorted({_DETAIL_FIELD_MAP[f] for f in fields if f in _DETAIL_FIELD_MAP})
    resp = requests.get(
        f"{BASE}/places/{place_id}", timeout=timeout,
        headers={"X-Goog-Api-Key": api_key,
                 "X-Goog-FieldMask": ",".join(mask) or "id"},
    )
    if resp.status_code != 200:
        err = _error(resp)
        err.pop("results", None)
        return err
    return {"status": "OK", "result": _to_legacy_place(resp.json())}


def find_place(api_key: str, text: str, timeout: int = 10) -> dict:
    """Find Place equivalent (Text Search, 1 result). Returns {"status","candidates"}.

    Candidates include website/phone when available (legacy Find Place could not).
    Works for plain-text and E.164 phone-number inputs.
    """
    mask = ("places.id,places.displayName,places.formattedAddress,places.location,"
            "places.websiteUri,places.nationalPhoneNumber")
    resp = requests.post(
        f"{BASE}/places:searchText", json={"textQuery": text, "pageSize": 1},
        timeout=timeout,
        headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": mask},
    )
    if resp.status_code != 200:
        err = _error(resp)
        err["candidates"] = err.pop("results", [])
        return err
    cands = [_to_legacy_place(p) for p in resp.json().get("places", [])]
    return {"status": "OK" if cands else "ZERO_RESULTS", "candidates": cands}


class Client:
    """Minimal drop-in for ``googlemaps.Client`` (``places`` and ``place`` only)."""

    def __init__(self, key: str):
        if not key:
            raise ValueError("Must provide API key")
        self.key = key
        self._last_query = None

    def places(self, query=None, page_token=None, **_ignored):
        # The New API needs the original textQuery on follow-up pages (legacy didn't).
        if query:
            self._last_query = query
        else:
            query = self._last_query
        data = text_search(self.key, query=query, page_token=page_token)
        # googlemaps raises on non-OK/ZERO_RESULTS; mirror that so callers'
        # existing try/except handling still triggers.
        if data["status"] not in ("OK", "ZERO_RESULTS"):
            raise RuntimeError(f"Places API (New) {data['status']}: {data.get('error_message', '')}")
        return data

    def place(self, place_id, fields=None, **_ignored):
        data = place_details(self.key, place_id, fields=fields)
        if data["status"] != "OK":
            raise RuntimeError(f"Places API (New) {data['status']}: {data.get('error_message', '')}")
        return data


class _LegacyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


def legacy_get(url: str, params: Optional[dict] = None, timeout: int = 10, **_ignored):
    """requests.get stand-in for the legacy Places web-service URLs."""
    params = params or {}
    key = params.get("key", "")
    if url.endswith("/textsearch/json"):
        return _LegacyResponse(text_search(
            key, query=params.get("query"), page_token=params.get("pagetoken"),
            timeout=timeout))
    if url.endswith("/findplacefromtext/json"):
        return _LegacyResponse(find_place(key, params.get("input", ""), timeout=timeout))
    if url.endswith("/details/json"):
        return _LegacyResponse(place_details(
            key, params.get("place_id", ""), fields=params.get("fields"), timeout=timeout))
    raise ValueError(f"Unsupported legacy Places URL: {url}")
