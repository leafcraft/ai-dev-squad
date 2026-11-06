"""Per-session scratch state for the dev squad's bounded back-edges.

Used for the cycles that MUST terminate (Rule 3 — bound every retry
back-edge), counted across a ticket's life:

  * the planner's in-flight ticket + plan (recovered on a replan)
  * reviewer ⇄ developer rework rounds (the code-review fix loop)
  * qa ⇄ developer rework rounds (the QA fix loop) → escalate at the cap

In-process dict keyed by session_id. Single-replica dev default. For a
multi-replica deployment, swap the three primitives below for Redis
hashes (the SDK's Redis client is available on the mesh instance).
"""
from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_STASH: dict[str, dict[str, Any]] = {}


def stash_get(session_id: str) -> dict[str, Any]:
    with _LOCK:
        return dict(_STASH.get(session_id or "_nosession", {}))


def stash_put(session_id: str, data: dict[str, Any]) -> None:
    with _LOCK:
        _STASH[session_id or "_nosession"] = dict(data)


def stash_update(session_id: str, patch: dict[str, Any]) -> None:
    """Merge `patch` into the session bucket (does NOT wipe rework counters /
    claim markers, unlike stash_put which replaces the whole bucket)."""
    sid = session_id or "_nosession"
    with _LOCK:
        _STASH.setdefault(sid, {}).update(patch)


def claim(session_id: str, key: str) -> bool:
    """ATOMIC one-shot claim within a session — True for the FIRST caller only.

    The parallel QA testers (scaling.copies.count: N) share one session; the first copy to
    claim a fresh PR (`claim(sid, "pr:<url>")`) verifies it, the others fall
    through to the assigned-test backlog. In-process + under lock, so the N
    copies in one process race safely."""
    sid = session_id or "_nosession"
    ck = f"__claim__:{key}"
    with _LOCK:
        bucket = _STASH.setdefault(sid, {})
        if bucket.get(ck):
            return False
        bucket[ck] = True
        return True


def bump(session_id: str, key: str, by: int = 1) -> int:
    """Increment a counter and return the NEW value."""
    sid = session_id or "_nosession"
    with _LOCK:
        bucket = _STASH.setdefault(sid, {})
        bucket[key] = int(bucket.get(key, 0)) + by
        return bucket[key]


def peek(session_id: str, key: str, default: int = 0) -> int:
    sid = session_id or "_nosession"
    with _LOCK:
        return int(_STASH.get(sid, {}).get(key, default))
