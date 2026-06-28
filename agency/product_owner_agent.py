"""product_owner_agent — grooms a raw request into a well-formed ticket.

Upstream of the planner: turns free-text (a stakeholder request, or a
bare Jira ticket created elsewhere) into a ticket the squad can actually
build — crisp title, description, TESTABLE acceptance_criteria (the same
conditions QA verifies), priority, and a QA discipline hint. Dedupes
against the open backlog so it doesn't file the same thing twice.

@pre_compose  load_request_context — pull the raw request text + the open
              ticket titles (the dedupe signal) into prepared_data.
@chain        commit_ticket — deterministic: file the groomed ticket to
              Jira (dev: the store) as status "triage" so the planner's
              autonomous poll won't grab it until a stakeholder signs off
              scope; the stakeholder-approve route then hands it on.
Runtime tool  jira_update — comment on the ticket as it's groomed; the
              @chain guarantees the create regardless.
"""
from __future__ import annotations
from typing import Any
import json, re

from leafmesh import LeafMeshLogger, pre_compose, chain
from agency._shared import store

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "title": "", "description": "", "acceptance_criteria": [], "priority": "medium",
    "discipline": "backend", "ticket_ready": False, "duplicate_of": "",
    "not_actionable": False, "ticket_id": "", "ticket_key": "", "ticket_url": "",
}
_DISCIPLINES = ("backend", "api", "frontend")
_PRIORITIES = ("critical", "high", "medium", "low")


def load_request_context(data: Any, ctx: Any) -> dict[str, Any]:
    req = ""
    if isinstance(data, dict):
        req = str(data.get("request") or data.get("human_message") or "")
        if not req:
            owd = data.get("original_webhook_data")
            if isinstance(owd, dict):
                req = str(owd.get("request") or owd.get("summary") or owd.get("text") or "")
        if not req:                                  # event-driven: a raw ticket created
            req = str(data.get("summary") or data.get("title") or "")
    return {"raw_request": req or "(no request text provided)",
            "open_titles": store.open_ticket_titles()}


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


async def commit_ticket(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if result.get("ticket_ready") and not result.get("duplicate_of"):
        rec = store.create_ticket(
            title=result.get("title") or "Untitled request",
            description=result.get("description", ""),
            priority=result.get("priority", "medium"),
            acceptance_criteria=result.get("acceptance_criteria") or [],
            discipline=result.get("discipline", "backend"),
        )
        result["ticket_id"] = rec["ticket_id"]
        result["ticket_key"] = rec["key"]
        result["ticket_url"] = rec["url"]
        logger.info(f"[po] filed {rec['key']} ({result['priority']}/{result['discipline']}) "
                    f"— triage, awaiting stakeholder sign-off")
    return result


@pre_compose(context_processor=load_request_context)
@chain(commit_ticket)
async def product_owner_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    llm = _parse(llm_response)
    out = {**_DEFAULTS}
    priority = str(llm.get("priority") or "medium").lower()
    discipline = str(llm.get("discipline") or "backend").lower()
    out.update({
        "title": str(llm.get("title") or ""),
        "description": str(llm.get("description") or ""),
        "acceptance_criteria": list(llm.get("acceptance_criteria") or []),
        "priority": priority if priority in _PRIORITIES else "medium",
        "discipline": discipline if discipline in _DISCIPLINES else "backend",
        "ticket_ready": bool(llm.get("ticket_ready")),
        "duplicate_of": str(llm.get("duplicate_of") or ""),
        "not_actionable": bool(llm.get("not_actionable")),
    })
    # never queue a duplicate or an un-groomable request
    if out["duplicate_of"] or out["not_actionable"] or not out["title"]:
        out["ticket_ready"] = False
    logger.info(f"[po] groomed: ready={out['ticket_ready']} "
                f"dup={out['duplicate_of'] or '-'} actionable={not out['not_actionable']}")
    return out
