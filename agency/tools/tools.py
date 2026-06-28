"""Runtime tools for the AI dev squad (@global_tool).

`jira_update` is the WRITE tool the LLM agents call mid-run to move a
ticket through its lifecycle — set status, add a comment, link the PR.
DEV writes to the local store; PROD swaps the body for a Jira MCP call.
The deterministic FINAL writes also happen in each agent's @chain /
shaper, so the lifecycle is correct even if the model forgets to call
the tool — tool for in-the-moment updates, chain for the guarantee.
"""
from __future__ import annotations
from typing import Any
from leafmesh import LeafMeshLogger, global_tool
from agency._shared import store

logger = LeafMeshLogger(__name__)

_VALID = {"In Progress", "In Review", "Done", "Blocked", "open"}


@global_tool
def jira_update(ticket_key: str, status: str = "", comment: str = "", pr_url: str = "") -> dict[str, Any]:
    """Update a Jira ticket: set `status` (In Progress|In Review|Done|Blocked),
    add a `comment`, and/or `link a PR`. Call this as you work a ticket."""
    done = []
    if status:
        if status not in _VALID:
            return {"ok": False, "error": f"status must be one of {sorted(_VALID)}"}
        store.set_status(ticket_key, status); done.append(f"status={status}")
    if comment:
        store.add_comment(ticket_key, comment); done.append("comment")
    if pr_url:
        store.link_pr(ticket_key, pr_url); done.append("pr_linked")
    logger.info(f"[jira_update] {ticket_key}: {', '.join(done) or 'noop'}")
    return {"ok": True, "ticket_key": ticket_key, "applied": done}


@global_tool
def jira_create_bug(summary: str, severity: str = "major", discipline: str = "backend",
                    description: str = "", parent_key: str = "") -> dict[str, Any]:
    """File a bug back into Jira for a defect found in QA. `severity` is
    blocker|major|minor; `discipline` is backend|api|frontend; `parent_key`
    links it to the ticket under test. Call this for each defect you find.
    DEV writes to the local store; PROD swaps the body for a Jira MCP call.
    QA's @sdk.intelligence shaper files bugs deterministically too — this
    tool is for in-the-moment filing, the shaper is the guarantee."""
    sev = severity if severity in ("blocker", "major", "minor") else "major"
    disc = discipline if discipline in ("backend", "api", "frontend") else "backend"
    key = store.create_bug(summary, description, sev, disc, parent_key, reporter="qa")
    logger.info(f"[jira_create_bug] {key} ({sev}/{disc}) parent={parent_key or '-'}")
    return {"ok": True, "bug_key": key, "severity": sev, "discipline": disc}
