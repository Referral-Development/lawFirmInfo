"""Google place_id (ChIJ...) -> Google's official place link (https://maps.google.com/?cid=N), no API call.
The ChIJ id is a protobuf holding two fixed64 values (feature id); cid is the second one as an unsigned decimal.
Verified against Places API googleMapsUri (see data/verify_cid.py)."""
import base64, re, struct


def place_id_to_cid(place_id: str):
    try:
        b = base64.urlsafe_b64decode(place_id + "=" * (-len(place_id) % 4))
        # 0x0a <len> 0x09 <8 bytes> 0x11 <8 bytes>
        i = b.index(b"\x09")
        a, c = struct.unpack("<QQ", b[i + 1:i + 9] + b[i + 10:i + 18])
        assert b[i + 9] == 0x11
        return str(c)
    except Exception:
        return None


def gbp_url_from_place_id(place_id: str):
    cid = place_id_to_cid(place_id)
    return f"https://maps.google.com/?cid={cid}" if cid else None


_PID = re.compile(r"place_id:([\w-]+)")


def convert_maps_url(url: str):
    """Convert an old 'google.com/maps/place/?q=place_id:X' link; returns None if not convertible."""
    m = _PID.search(url or "")
    return gbp_url_from_place_id(m.group(1)) if m else None
