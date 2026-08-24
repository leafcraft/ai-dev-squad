"""merge_coordinator_agent — the programmatic FINISHER / actuator.

Sits BEHIND the human merge gate (tech_lead 'merge', senior 'approve'). It never
merges on its own say-so. It is the concrete "@chain fails closed":

  a) approval RE-CHECK — reaching this agent does NOT mean approved. An
     allow-token allowlist + a block-token denylist; an UNKNOWN verdict fails
     closed (not merged).
  b) deterministic invariants a human can't wave through: a PR on record, CI
     green, the ticket known.
  c) render the real release record to ./out; on a tripped floor, DELETE the
     partial and record status=blocked with reasons — never a half-merged claim.
  d) recovery across the HITL gate — after the human approves, the SDK re-delivers
     only human-response meta; the pr_url/ticket flowing through the chain is
     dropped. Recover it: upstream → session stash → dev store.

The one back-edge (CI red → developer) is BOUNDED by a counter → escalate to a
human at the cap, never an endless loop. PROD: wire GitHub (CI + merge) + Jira
(transition); DEV: the store stands in and ANNOUNCES itself (green by default,
no GITHUB_MCP_URL wired).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from leafmesh import LeafMeshLogger, chain
from agency._shared import store, session_stash

logger = LeafMeshLogger(__name__)

_OUT_DIR = Path("./out")
_MAX_MERGE_FIX_ROUNDS = 3
_APPROVE_TOKENS = frozenset({"merge", "approve", "approved", "ship", "shipit",
                             "lgtm", "yes", "ok"})
_BLOCK_TOKENS = frozenset({"reject", "rejected", "hold", "held", "held_for_review",
                           "no", "deny", "denied", "changes", "request_changes"})

_DEFAULTS: dict[str, Any] = {
    "pr_url": "", "ticket_id": "", "ticket_key": "", "ci_status": "",
    "approved": False, "merged": False, "needs_fix": False, "escalate": False,
    "artifact_path": "", "status": "", "block_reasons": [],
}


def _signal(input_data: dict) -> str:
    """The human verdict, from wherever the SDK delivered it (shape-tolerant)."""
    sources = [input_data]
    up = input_data.get("upstream_yields")
    if isinstance(up, dict):
        sources.extend(v for v in up.values() if isinstance(v, dict))
    for src in sources:
        for key in ("human_message", "human_decision", "decision", "message"):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
    return ""


def _is_approved(input_data: dict) -> bool:
    """Fail closed: only an explicit approve token counts; unknown => not approved."""
    sig = _signal(input_data)
    if sig in _BLOCK_TOKENS:
        return False
    return sig in _APPROVE_TOKENS


def _is_blocked(input_data: dict) -> bool:
    return _signal(input_data) in _BLOCK_TOKENS


def _sid(input_data: Any, context: Any) -> str:
    for s in (context, input_data):
        if isinstance(s, dict):
            if s.get("session_id"):
                return str(s["session_id"])
            owd = s.get("original_webhook_data")
            if isinstance(owd, dict) and owd.get("session_id"):
                return str(owd["session_id"])
    return ""


def _pick(input_data: Any, key: str, default: Any = "") -> Any:
    if isinstance(input_data, dict):
        if input_data.get(key) not in (None, ""):
            return input_data[key]
        uy = input_data.get("upstream_yields")
        if isinstance(uy, dict):
            if uy.get(key) not in (None, ""):
                return uy[key]
            for v in uy.values():
                if isinstance(v, dict) and v.get(key) not in (None, ""):
                    return v[key]
    return default


def _recover(input_data: dict, context: dict) -> dict:
    """Recover pr_url/ticket across the HITL merge gate: upstream → stash → store."""
    pr = str(_pick(input_data, "pr_url") or "")
    tid = str(_pick(input_data, "ticket_id") or "")
    key = str(_pick(input_data, "ticket_key") or "")
    if pr:
        return {"pr_url": pr, "ticket_id": tid, "ticket_key": key}
    sid = _sid(input_data, context)
    stashed = session_stash.stash_get(sid)
    if stashed.get("pr_url"):
        return {"pr_url": str(stashed.get("pr_url")),
                "ticket_id": str(stashed.get("ticket_id") or tid),
                "ticket_key": str(stashed.get("ticket_key") or key)}
    persisted = store.get_under_review(sid)
    if persisted.get("pr_url"):
        return {"pr_url": str(persisted.get("pr_url")),
                "ticket_id": str(persisted.get("ticket_id") or tid),
                "ticket_key": str(persisted.get("ticket_key") or key)}
    return {"pr_url": pr, "ticket_id": tid, "ticket_key": key}


def _render_release(pack: dict) -> str:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    key = str(pack.get("ticket_key") or pack.get("ticket_id") or "merge")
    path = _OUT_DIR / f"{key}_release_record.md"
    lines = [f"# Release record — {key}",
             f"PR: {pack.get('pr_url', '')}",
             f"Ticket: {pack.get('ticket_id', '')} ({key})",
             f"CI: {pack.get('ci_status', '')}",
             "",
             "_Merged after a re-checked human approval and a green CI gate._",
             "_Recorded in the WORM audit ledger._"]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _delete_partial(result: dict) -> None:
    p = result.get("artifact_path")
    if p:
        Path(p).unlink(missing_ok=True)
        result["artifact_path"] = ""


def enforce_merge_floor(result: Any, context: dict) -> dict:
    """The finisher floor — merge ONLY on a real approval + a green CI gate."""
    result = result if isinstance(result, dict) else {}
    result.setdefault("block_reasons", [])
    inp = result.pop("_input", {}) if isinstance(result.get("_input"), dict) else {}
    sid = result.pop("_sid", "") or ""

    # (a) approval re-check — fail closed on anything that isn't an approve token.
    if not result.get("approved"):
        result["status"] = "blocked"
        if _is_blocked(inp):
            result["block_reasons"].append("human rejected / held the merge")
        else:
            result["block_reasons"].append("no explicit human approval on record (fail closed)")
        _delete_partial(result)
        logger.info(f"[merge] floor BLOCKED: {result['block_reasons']}")
        return result

    # (b) invariant — a PR must be on record (recovered across the gate).
    pr = str(result.get("pr_url") or "")
    if not pr:
        result.update(status="blocked", merged=False)
        result["block_reasons"].append("no PR on record across the merge gate (fail closed)")
        _delete_partial(result)
        logger.warning("[merge] no PR recovered — BLOCKED")
        return result

    # (c) CI gate — never merge a red build. Dev store announces itself.
    if not os.getenv("GITHUB_MCP_URL"):
        logger.info("[merge] dev-store CI (no GITHUB_MCP_URL wired) — green by default")
    ci = store.get_ci_status(pr)
    result["ci_status"] = ci
    if ci != "green":
        rounds = session_stash.bump(sid, f"merge_fix:{pr}")
        result["needs_fix"] = True
        if rounds > _MAX_MERGE_FIX_ROUNDS:
            result.update(escalate=True, status="escalated")
            logger.warning(f"[merge] CI {ci} — rework cap hit ({rounds}); escalating to the tech lead")
        else:
            result["status"] = "needs_fix"
            logger.warning(f"[merge] CI {ci} on {pr} — NOT merging; back to developer (round {rounds})")
        _delete_partial(result)
        return result

    # Passed every floor — render the record, do the real merge, ledger it.
    result["artifact_path"] = _render_release(result)
    store.merge_pr(pr)
    key = str(result.get("ticket_key") or "")
    if key:
        store.set_status(key, "Done")
    if result.get("ticket_id"):
        store.clear_in_flight(str(result["ticket_id"]))     # frees a WIP slot
    store.append_release_ledger({"pr_url": pr, "ticket_id": result.get("ticket_id"),
                                 "ticket_key": key, "event": "merged"})
    result.update(merged=True, status="merged")
    logger.info(f"[merge] merged {pr}; {key or '?'} → Done; WIP slot freed; "
                f"record → {result['artifact_path']}")
    return result


@chain(enforce_merge_floor)
async def merge_coordinator_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    ctx = context if isinstance(context, dict) else {}
    rec = _recover(input_data, ctx)
    sid = _sid(input_data, ctx)
    key = rec.get("ticket_key") or (store.get_ticket(rec["ticket_id"]).get("key", "") if rec.get("ticket_id") else "")

    out = {**_DEFAULTS,
           "pr_url": rec.get("pr_url", ""), "ticket_id": rec.get("ticket_id", ""),
           "ticket_key": key,
           "_input": input_data, "_sid": sid}
    if _is_approved(input_data):
        out["approved"] = True          # the floor re-checks + issues the merge
    logger.info(f"[merge] recovered pr={out['pr_url'] or '(none)'} approved={out['approved']}")
    return out
