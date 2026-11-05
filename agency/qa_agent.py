"""qa_agent — Jira-driven QA engineer (Claude Agent SDK executor), FANNED.

Runs as N PARALLEL testers (config: scaling.copies.count: 3, copies.handoff: each).
Each copy makes an ATOMIC claim in pre_compose so copies never duplicate work:

  * a fresh PR (developer hand-off) is claimed by exactly ONE copy
    (session_stash.claim) — that copy verifies the build; the others fall
    through and drain the assigned-test backlog.
  * standalone copies each atomically pop the next assigned QA/test ticket
    (store.claim_next_qa) — disjoint tickets, real parallelism by discipline.
  * a copy that claims nothing goes IDLE (did_work=false, route="") — it routes
    NOWHERE, so an empty tester never emits a spurious audit entry.

Because scaling.copies.handoff is `each`, every copy routes its OWN result through
the single computed `route` field — there is no cross-copy merge, so none of the
number-sum / last-wins merge footguns apply.

@pre_compose  load_test_target — claim the work + assemble the SDK run brief.
Shaper        qa_agent(connector_result, …) — map the SDK verdict into yields,
              FILE every defect as a Jira bug (deterministic), bound the
              qa⇄developer rework loop, and compute `route`.

QA never edits the repo (permission_mode: default — no Write/Edit): it VERIFIES
and reports. The bugs it files become new Jira tickets the planner later picks
up — that's how the squad closes the find→fix loop. Discipline comes from
QA_DISCIPLINE (backend | api | frontend).
"""
from __future__ import annotations
from typing import Any
import json, os, re

from leafmesh import LeafMeshLogger, pre_compose
from agency._shared import store, session_stash

logger = LeafMeshLogger(__name__)

_DISCIPLINE = (os.getenv("QA_DISCIPLINE", "backend") or "backend").strip().lower()
_MAX_REWORK_ROUNDS = 3
_DEFAULTS: dict[str, Any] = {
    "ticket_id": "", "pr_url": "", "discipline": _DISCIPLINE,
    "qa_verdict": "", "qa_escalate": False, "from_dev_flow": False,
    "did_work": False, "route": "",
    "bugs_found": [], "bugs_filed": [], "tests_run": 0,
}


def _pick(data: Any, key: str, default: Any = "") -> Any:
    if isinstance(data, dict):
        if data.get(key) not in (None, ""):
            return data[key]
        uy = data.get("upstream_yields")
        if isinstance(uy, dict):
            if uy.get(key) not in (None, ""):
                return uy[key]
            for v in uy.values():
                if isinstance(v, dict) and v.get(key) not in (None, ""):
                    return v[key]
    return default


def _prepared(context: Any) -> dict[str, Any]:
    if isinstance(context, dict) and isinstance(context.get("prepared_data"), dict):
        return context["prepared_data"]
    return {}


def _sid(data: Any, context: Any) -> str:
    for s in (context, data):
        if isinstance(s, dict) and s.get("session_id"):
            return str(s["session_id"])
    return ""


def load_test_target(data: Any, ctx: Any) -> dict[str, Any]:
    """Claim ONE unit of work for THIS tester copy, then build the test brief.

    Ordering: a fresh PR (claimed once across the copies) beats the backlog, so
    the developer's hand-off is always verified before idle testers drain
    tickets. A copy that claims nothing returns mode="idle"."""
    sid = _sid(data, ctx if isinstance(ctx, dict) else {})
    pr = str(_pick(data, "pr_url") or "")
    tid = str(_pick(data, "ticket_id") or "")

    # Inline fresh PR — exactly one copy claims it and verifies the build.
    if pr or tid:
        if session_stash.claim(sid, f"pr:{pr or tid}"):
            t = store.get_ticket(tid) if tid else {}
            return {"mode": "pr", "discipline": _DISCIPLINE, "pr_url": pr,
                    "ticket": t, "acceptance_criteria": t.get("acceptance_criteria", []),
                    "from_dev_flow": True, "did_work": True}
        # Lost the PR race → this copy drains the backlog instead (fall through).

    # Standalone — atomically claim the next assigned QA/test ticket.
    pick = store.claim_next_qa(discipline=_DISCIPLINE)
    if not pick:
        return {"mode": "idle", "discipline": _DISCIPLINE, "pr_url": "",
                "ticket": {}, "acceptance_criteria": [],
                "from_dev_flow": False, "did_work": False}
    return {"mode": "ticket", "discipline": _DISCIPLINE, "pr_url": "",
            "ticket": pick, "acceptance_criteria": pick.get("acceptance_criteria", []),
            "from_dev_flow": False, "did_work": True}


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


def _file_bugs(bugs: list[Any], discipline: str, parent_key: str) -> list[str]:
    filed: list[str] = []
    for b in bugs:
        if not isinstance(b, dict):
            continue
        title = str(b.get("title") or b.get("summary") or "Defect found in QA")
        desc = "\n".join(f"{k}: {b.get(k)}" for k in ("steps", "expected", "actual") if b.get(k))
        sev = str(b.get("severity") or "major").lower()
        sev = sev if sev in ("blocker", "major", "minor") else "major"
        filed.append(store.create_bug(title, desc, sev, discipline, parent_key))
    return filed


@pre_compose(context_processor=load_test_target)
async def qa_agent(connector_result: Any, input_data: Any, context: Any) -> dict[str, Any]:
    out = {**_DEFAULTS}
    pd = _prepared(context)
    did_work = bool(pd.get("did_work"))

    # Idle copy — claimed nothing. Route NOWHERE (terminal, no spurious audit).
    if not did_work:
        logger.info(f"[qa:{_DISCIPLINE}] idle — no work to claim (route='')")
        return out

    tk = pd.get("ticket") if isinstance(pd.get("ticket"), dict) else {}
    pr = str(pd.get("pr_url") or _pick(input_data, "pr_url") or "")
    tid = str((tk.get("ticket_id") if tk else "") or _pick(input_data, "ticket_id") or "")
    from_dev_flow = bool(pd.get("from_dev_flow"))
    parent = (store.get_ticket(tid).get("key", "") if tid else "") or (tk.get("key", "") if tk else "")
    out.update({"ticket_id": tid, "pr_url": pr, "from_dev_flow": from_dev_flow, "did_work": True})

    if isinstance(connector_result, dict) and connector_result:
        text = str(connector_result.get("result") or connector_result.get("text") or "")
        parsed = _parse(text)
        bugs = parsed.get("bugs")
        if not isinstance(bugs, list):
            cr_bugs = connector_result.get("bugs")
            bugs = cr_bugs if isinstance(cr_bugs, list) else []
        verdict = str(parsed.get("qa_verdict") or connector_result.get("qa_verdict") or "").lower()
        if verdict not in ("pass", "fail"):
            verdict = "fail" if bugs else "pass"
        try:
            out["tests_run"] = int(parsed.get("tests_run", connector_result.get("tests_run", 0)) or 0)
        except (TypeError, ValueError):
            out["tests_run"] = 0
        out["bugs_found"] = bugs
        out["qa_verdict"] = verdict
        out["bugs_filed"] = _file_bugs(bugs, _DISCIPLINE, parent)
    else:
        # Dev stub — no SDK/repo: pass with no bugs so the chain runs.
        out["qa_verdict"] = "pass"
        logger.info(f"[qa:{_DISCIPLINE}] DEV STUB — would run test cases via the Claude Agent SDK")

    # Bound the qa⇄developer rework loop (only the inline fresh-PR path loops).
    if out["qa_verdict"] == "fail" and from_dev_flow:
        rounds = session_stash.bump(_sid(input_data, context if isinstance(context, dict) else {}),
                                    f"qa_rework:{pr or tid}")
        if rounds > _MAX_REWORK_ROUNDS:
            out["qa_escalate"] = True
            logger.warning(f"[qa:{_DISCIPLINE}] rework cap hit ({rounds}) — escalating to the tech lead")

    # Lifecycle: a failing QA on a PR sends the ticket back to In Progress;
    # a standalone test ticket frees its slot when done.
    if parent and from_dev_flow:
        store.set_status(parent, "In Progress" if out["qa_verdict"] == "fail" else "In Review")
    if not from_dev_flow and tid:
        store.qa_clear_in_flight(tid)

    # Compute the SINGLE routing signal (copies.handoff: each → per-copy).
    if from_dev_flow:
        if out["qa_verdict"] == "pass":
            out["route"] = "reviewer"
        elif out["qa_escalate"]:
            out["route"] = "escalate"
        else:
            out["route"] = "developer"
    else:
        out["route"] = "audit"   # standalone test ticket — bugs filed are the output

    logger.info(f"[qa:{_DISCIPLINE}] verdict={out['qa_verdict']} route={out['route']} "
                f"bugs={len(out['bugs_found'])} filed={out['bugs_filed'] or '-'}")
    return out
