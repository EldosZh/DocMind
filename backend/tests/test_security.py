from uuid import uuid4

from backend.app.security import decode_access, issue_access, new_refresh, refresh_digest


def test_access_token_round_trip():
    subject = uuid4()
    assert decode_access(issue_access(subject)) == subject

def test_refresh_tokens_are_random_and_only_digest_stored():
    raw, digest, expiry = new_refresh()
    assert raw and digest == refresh_digest(raw) and raw != digest
    assert expiry.tzinfo is not None
