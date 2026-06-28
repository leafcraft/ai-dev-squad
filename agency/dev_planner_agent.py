"""dev_planner_agent — pulls assigned Jira tickets, picks one, plans it.

@pre_compose  load_queue — pull the squad's open tickets (excludes those
              already in-flight), AND, on a replan, recover the ticket
              under review + the lead's feedback from the session stash so
              the new plan actually addresses it.
@chain        commit_pick — deterministic: mark the ticket in-flight (the
              idempotency + WIP guard), move it to "In Progress" in Jira,
              persist the plan, stash it for a possible replan.
Runtime tool  jira_update — the LLM may comment the plan onto the ticket
              as it works (write tooling); the @chain guarantees the
              status write regardless.
"""
from __future__ import annotations
from typing import Any
import json, os, re

from leafmesh import LeafMeshLogger, pre_compose, chain
from agency._shared import store, session_stash

logger = LeafMeshLogger(__name__)

_MAX_WIP = int(os.getenv("DEV_SQUAD_MAX_WIP", "1") or "1")   # serial by default
_DEFAULTS: dict[str, Any] = {
    "ticket_id": "", "ticket_key": "", "ticket_url": "", "plan": "",
    "plan_ready": False, "no_actionable_ticket": False, "priority_rank": 0,
}


def _sid(input_data: Any, context: Any) -> str:
    for s in (context, input_data):
        if isinstance(s, dict):
            if s.get("session_id"):
                return str(s["session_id"])
            owd = s.get("original_webhook_data")
            if isinstance(owd, dict) and owd.get("session_id"):
                return str(owd["session_id"])
    return ""


def _directed_id(input_data: Any) -> str:
    """A stakeholder-approved ticket handed straight to the planner — plan
    THIS one (it's in 'triage', so the autonomous queue won't surface it)."""
    if not isinstance(input_data, dict):
        return ""
    frm = str(input_data.get("from_agent") or input_data.get("source_agent") or "")
    msg = str(input_data.get("human_message") or "")
    if frm != "stakeholder_human" and msg != "approve":
        return ""
    tid = input_data.get("ticket_id")
    if not tid:
        uy = input_data.get("upstream_yields")
        if isinstance(uy, dict):
            tid = uy.get("ticket_id")
            if not tid:
                for v in uy.values():
                    if isinstance(v, dict) and v.get("ticket_id"):
                        tid = v["ticket_id"]; break
    return str(tid or "")


def _is_replan(input_data: Any) -> tuple[bool, str]:
    if not isinstance(input_data, dict):
        return False, ""
    msg = str(input_data.get("human_message") or "")
    frm = str(input_data.get("from_agent") or input_data.get("source_agent") or "")
    if msg == "replan" or frm == "tech_lead_human":
        # feedback may be in human_message free-text, human_data, or notes
        fb = ""
        hd = input_data.get("human_data")
        if isinstance(hd, dict):
            fb = str(hd.get("notes") or hd.get("feedback") or "")
        fb = fb or str(input_data.get("notes") or "")
        return True, fb
    return False, ""


def load_queue(data: Any, ctx: Any) -> dict[str, Any]:
    replan, feedback = _is_replan(data)
    if replan:
        sid = _sid(data, ctx if isinstance(ctx, dict) else {})
        stashed = session_stash.stash_get(sid)
        tid = stashed.get("in_flight_ticket", "")
        t = store.get_ticket(tid) if tid else {}
        return {"mode": "replan", "replan_ticket": t,
                "lead_feedback": feedback or "Address the tech lead's concerns and revise the plan.",
                "assigned_tickets": [t] if t else []}
    directed = _directed_id(data)
    if directed:
        t = store.get_ticket(directed)
        return {"mode": "directed", "directed_ticket": t,
                "assigned_tickets": [t] if t else []}
    return {"mode": "fresh", "assigned_tickets": store.assigned_tickets()}


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


async def commit_pick(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if result.get("ticket_id") and result.get("plan_ready"):
        store.mark_in_flight(result["ticket_id"])          # idempotency + WIP guard
        if result.get("ticket_key"):
            store.set_status(result["ticket_key"], "In Progress")   # Jira lifecycle (deterministic)
        store.put_plan(result["ticket_id"], result.get("plan", ""))
        sid = _sid(result, ctx if isinstance(ctx, dict) else {})
        session_stash.stash_put(sid, {"in_flight_ticket": result["ticket_id"],
                                      "ticket_key": result.get("ticket_key", "")})
    return result


@pre_compose(context_processor=load_queue)
@chain(commit_pick)
async def dev_planner_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    out = {**_DEFAULTS}
    replan, _ = _is_replan(input_data)
    llm = _parse(llm_response)

    if replan:
        sid = _sid(input_data, context if isinstance(context, dict) else {})
        t = store.get_ticket(session_stash.stash_get(sid).get("in_flight_ticket", "")) or {}
        if not t:
            out["no_actionable_ticket"] = True
            return out
        out.update({"ticket_id": t["ticket_id"], "ticket_key": t.get("key", ""),
                    "ticket_url": t.get("url", ""),
                    "plan": str(llm.get("plan") or "Revised plan addressing the lead's feedback."),
                    "plan_ready": True})
        logger.info(f"[planner] REPLAN {out['ticket_key']} (feedback threaded)")
        return out

    # Directed: a stakeholder approved this specific ticket — plan it
    # (it's in 'triage', so the autonomous queue won't surface it).
    directed = _directed_id(input_data)
    if directed:
        if store.in_flight_count() >= _MAX_WIP:
            out["no_actionable_ticket"] = True
            logger.info(f"[planner] WIP cap {_MAX_WIP} reached — directed ticket queued")
            return out
        t = store.get_ticket(directed) or {}
        if not t:
            out["no_actionable_ticket"] = True
            return out
        out.update({"ticket_id": t["ticket_id"], "ticket_key": t.get("key", ""),
                    "ticket_url": t.get("url", ""),
                    "plan": str(llm.get("plan") or f"Implement {t.get('title','the ticket')}."),
                    "plan_ready": True})
        logger.info(f"[planner] directed pick {out['ticket_key']} (stakeholder-approved)")
        return out

    # Fresh: respect the WIP cap, then pick the top of the queue.
    if store.in_flight_count() >= _MAX_WIP:
        out["no_actionable_ticket"] = True
        logger.info(f"[planner] WIP cap {_MAX_WIP} reached — not picking")
        return out
    tickets = store.assigned_tickets()
    if not tickets:
        out["no_actionable_ticket"] = True
        return out
    pick = tickets[0]
    out.update({"ticket_id": pick["ticket_id"], "ticket_key": pick.get("key", ""),
                "ticket_url": pick.get("url", ""),
                "plan": str(llm.get("plan") or f"Implement {pick.get('title','the ticket')}."),
                "plan_ready": True})
    logger.info(f"[planner] picked {out['ticket_key']} — plan_ready")
    return out
