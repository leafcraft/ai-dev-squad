"""merge_coordinator_agent — the CI gate + the real merge.

The tech lead's 'merge' routes here (not straight to audit). This agent:
  1. CI GATE — checks the PR's CI status (GitHub checks). Red → route back
     to the developer with the failure; never merge a red build.
  2. MERGE — green → merge the PR (gh pr merge / GitHub API), move the
     ticket to "Done" in Jira, clear the in-flight flag (frees a WIP slot),
     and record it.
PROD: wire the GitHub + Jira connectors; DEV: the store stands in.
"""
from __future__ import annotations
from typing import Any
from leafmesh import LeafMeshLogger
from agency._shared import store

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "pr_url": "", "ticket_id": "", "ticket_key": "", "ci_status": "",
    "merged": False, "needs_fix": False,
}


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


async def merge_coordinator_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    out = {**_DEFAULTS}
    pr = str(_pick(input_data, "pr_url") or "")
    tid = str(_pick(input_data, "ticket_id") or "")
    out["pr_url"] = pr
    out["ticket_id"] = tid
    key = store.get_ticket(tid).get("key", "") if tid else ""
    out["ticket_key"] = key

    if not pr:
        out["needs_fix"] = True
        logger.warning("[merge] no PR to merge — routing back to fix")
        return out

    ci = store.get_ci_status(pr)
    out["ci_status"] = ci
    if ci != "green":
        out["needs_fix"] = True
        logger.warning(f"[merge] CI {ci} on {pr} — NOT merging; back to developer")
        return out

    store.merge_pr(pr)
    if key:
        store.set_status(key, "Done")
    if tid:
        store.clear_in_flight(tid)              # frees a WIP slot
    out["merged"] = True
    logger.info(f"[merge] merged {pr}; {key or '?'} → Done; WIP slot freed")
    return out
