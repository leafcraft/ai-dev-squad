"""code_reviewer_agent — reads the PR diff, judges it, bounds the rework loop.

@pre_compose  load_pr_diff — pull the PR diff (dev: store) into prepared_data.
@chain        route_review — enforce floors the LLM can't waive: a security/
              HIGH finding forces is_blocker; cross-cutting forces
              requires_senior; AND cap how many times one PR may bounce
              back to the developer (the reviewer⇄developer cycle bound —
              lesson from the finance/outbound loops).

The four routes are exhaustive and exclusive (config can_call):
  is_blocker                                  → audit (never merged)
  not blocker, requires_senior                → senior_engineer_human
  not blocker, not senior, verdict approve    → tech_lead_human (merge gate)
  not blocker, not senior, verdict changes    → developer_agent (bounded)
So the floor normalises the verdict to approve | changes | block, and a
'block' verdict always sets is_blocker.
"""
from __future__ import annotations
from typing import Any
import json, re

from leafmesh import LeafMeshLogger, pre_compose, chain
from agency._shared import store, session_stash

logger = LeafMeshLogger(__name__)

_MAX_REWORK_ROUNDS = 3
_DEFAULTS: dict[str, Any] = {
    "pr_url": "", "ticket_id": "", "verdict": "", "findings": [],
    "requires_senior": False, "is_blocker": False,
}
_HIGH = ("security", "secret", "sql injection", "data loss", "rce", "broken build", "credential")
_VERDICTS = ("approve", "changes", "block")


def load_pr_diff(data: Any, ctx: Any) -> dict[str, Any]:
    pr = ""
    if isinstance(data, dict):
        pr = data.get("pr_url") or (data.get("upstream_yields") or {}).get("pr_url") or ""
        if not pr:
            for v in (data.get("upstream_yields") or {}).values():
                if isinstance(v, dict) and v.get("pr_url"):
                    pr = v["pr_url"]; break
    return {"pr_diff": store.get_pr_diff(pr) if pr else "", "pr_url": pr}


def _parse(resp: Any) -> dict[str, Any]:
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, str):
        try:
            return json.loads(resp)
        except ValueError:
            m = re.search(r"\{.*\}", resp, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except ValueError:
                    pass
    return {}


def _sid(input_data: Any, context: Any) -> str:
    for s in (context, input_data):
        if isinstance(s, dict) and s.get("session_id"):
            return str(s["session_id"])
    return ""


async def route_review(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    # An unknown verdict ("request_changes", "lgtm?") is not an approval.
    if str(result.get("verdict") or "").lower() not in _VERDICTS:
        result["verdict"] = "changes"
    result["verdict"] = str(result["verdict"]).lower()
    if result["verdict"] == "block":
        result["is_blocker"] = True
    findings = result.get("findings") or []
    blob = " ".join(str(f) for f in findings).lower()
    # Floor: security/HIGH → blocker; never auto-approvable.
    if any(h in blob for h in _HIGH):
        result["is_blocker"] = True
        result["verdict"] = "block"
    # Bound the reviewer⇄developer cycle.
    if result.get("verdict") == "changes" and not result.get("is_blocker"):
        sid = _sid(result, ctx if isinstance(ctx, dict) else {})
        rounds = session_stash.bump(sid, f"rework:{result.get('pr_url','')}")
        if rounds > _MAX_REWORK_ROUNDS:
            result["requires_senior"] = True   # stop looping — escalate to a human
            logger.warning(f"[review] rework cap hit ({rounds}) — escalating to senior")
    # When this PR heads to a human merge gate (approve → tech lead, or
    # requires_senior → senior), stash the PR/ticket so the merge FINISHER can
    # recover it after the gate (the SDK re-delivers only human-response meta).
    if result.get("verdict") == "approve" or result.get("requires_senior"):
        sid = _sid(result, ctx if isinstance(ctx, dict) else {})
        pack = {"pr_url": result.get("pr_url", ""), "ticket_id": result.get("ticket_id", "")}
        tk = store.get_ticket(result.get("ticket_id", "")) if result.get("ticket_id") else {}
        pack["ticket_key"] = tk.get("key", "") if isinstance(tk, dict) else ""
        session_stash.stash_update(sid, pack)
        store.put_under_review(sid, pack)
    return result


@pre_compose(context_processor=load_pr_diff)
@chain(route_review)
async def code_reviewer_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    llm = _parse(llm_response)
    out = {**_DEFAULTS}
    out.update({
        "pr_url": str(llm.get("pr_url") or (input_data.get("pr_url") if isinstance(input_data, dict) else "") or ""),
        "ticket_id": str(llm.get("ticket_id") or ""),
        "verdict": str(llm.get("verdict") or "changes"),
        "findings": list(llm.get("findings") or []),
        "requires_senior": bool(llm.get("requires_senior")),
        "is_blocker": bool(llm.get("is_blocker")),
    })
    logger.info(f"[review] {out['pr_url'] or '?'} verdict={out['verdict']} (route_review enforces floors)")
    return out
