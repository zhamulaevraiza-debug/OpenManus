"""Small text helpers shared by agents, flows and event payloads."""


def truncate(text: str, limit: int) -> str:
    """Shorten ``text`` to at most ``limit`` characters, ending with a marker.

    The marker states how many characters were dropped, e.g.
    ``"...\\n…[truncated 1234 chars]"``. Texts within the limit are returned unchanged.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    # Size the marker for the worst case so the result never exceeds ``limit``.
    marker_len = len(f"\n…[truncated {len(text)} chars]")
    keep = max(limit - marker_len, 0)
    return f"{text[:keep]}\n…[truncated {len(text) - keep} chars]"
