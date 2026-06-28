"""Dev-mode store for the AI dev squad.

Stands in for Jira (tickets + status) and GitHub (PR diff, CI, merge) so
the planner→lead→build→review→merge loop runs before connectors are
wired. Replace each read/write with the matching MCP connector in prod;
the function signatures are the contract. The two integration points:
  * Jira   → wire ${JIRA_MCP_URL}   into the ticket read/write + create_bug
  * GitHub → wire ${GITHUB_MCP_URL} into get_pr_diff / get_ci_status / merge_pr
Setting those env vars alone does nothing until you wire the calls here.
"""
from __future__ import annotations
import json, threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_DIR = Path("./data")
SQUAD = "dev-squad-bot"

_SEED = [
    {"ticket_id": "t1", "key": "ENG-1201", "url": "https://jira.example/ENG-1201",
     "title": "Add rate-limit headers to public API", "priority": "high",
     "status": "open", "assignee": SQUAD, "est_effort": 3},
    {"ticket_id": "t2", "key": "ENG-1188", "url": "https://jira.example/ENG-1188",
     "title": "Fix flaky checkout integration test", "priority": "medium",
     "status": "open", "assignee": SQUAD, "est_effort": 2},
    {"ticket_id": "t3", "key": "ENG-1210", "url": "https://jira.example/ENG-1210",
     "title": "Blocked: migrate auth to OIDC", "priority": "high",
     "status": "blocked", "assignee": SQUAD, "est_effort": 8},
]
_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _now(): return datetime.now(timezone.utc).isoformat()
def _path(k): _DIR.mkdir(parents=True, exist_ok=True); return _DIR/f"{k}.json"
def _load(k):
    p=_path(k)
    if not p.exists(): return {}
    try: return json.loads(p.read_text())
    except (ValueError, OSError): return {}
def _save(k, d):
    p=_path(k); t=p.with_suffix(".json.tmp"); t.write_text(json.dumps(d, indent=2, default=str)); t.replace(p)


def _tickets() -> dict[str, Any]:
    s = _load("tickets")
    if not s:
        s = {t["ticket_id"]: t for t in _SEED}; _save("tickets", s)
    return s


def assigned_tickets(assignee: str = SQUAD, exclude_in_flight: bool = True) -> list[dict[str, Any]]:
    """Open, actionable tickets for the squad, priority→effort. PROD: Jira search."""
    rows = [t for t in _tickets().values()
            if t.get("assignee") == assignee and t.get("status") == "open"]
    if exclude_in_flight:
        flying = set(_load("in_flight").keys())
        rows = [t for t in rows if t["ticket_id"] not in flying]
    rows.sort(key=lambda t: (_RANK.get(t.get("priority", "low"), 3), int(t.get("est_effort", 99))))
    return rows


def get_ticket(ticket_id: str) -> dict[str, Any]:
    return _tickets().get(ticket_id, {})


def open_ticket_titles(assignee: str = SQUAD) -> list[str]:
    """Titles of the open backlog — the product owner's dedupe signal."""
    return [t.get("title", "") for t in _tickets().values()
            if t.get("status") == "open" and t.get("assignee") == assignee and t.get("title")]


def create_ticket(title: str, description: str = "", priority: str = "medium",
                  acceptance_criteria: list[str] | None = None,
                  discipline: str = "backend", assignee: str = SQUAD) -> dict[str, Any]:
    """File a new ticket (product owner). Status is "triage" — invisible to
    the planner's autonomous poll until a stakeholder approves and the
    directed-pick route hands it on. PROD: Jira create-issue."""
    with _LOCK:
        s = _tickets()
        seq = _load("eng_seq") or {}
        n = int(seq.get("n", 1300)) + 1
        seq["n"] = n; _save("eng_seq", seq)
        tid, key = f"e{n}", f"ENG-{n}"
        rec = {"ticket_id": tid, "key": key, "url": f"https://jira.example/{key}",
               "title": title, "description": description, "priority": priority,
               "status": "triage", "assignee": assignee, "est_effort": 3,
               "acceptance_criteria": acceptance_criteria or [],
               "discipline": discipline, "created_at": _now()}
        s[tid] = rec; _save("tickets", s)
    return rec


# ── Jira write-back (the lifecycle) ──────────────────────────────────
def set_status(ticket_key: str, status: str) -> None:
    """PROD: Jira transition. DEV: stamp the ticket record."""
    with _LOCK:
        s = _tickets()
        for t in s.values():
            if t.get("key") == ticket_key:
                t["status"] = status; t["status_updated"] = _now()
        _save("tickets", s)


def add_comment(ticket_key: str, text: str) -> None:
    with _LOCK:
        c = _load("ticket_comments"); c.setdefault(ticket_key, []).append({"at": _now(), "text": text}); _save("ticket_comments", c)


def link_pr(ticket_key: str, pr_url: str) -> None:
    with _LOCK:
        s = _tickets()
        for t in s.values():
            if t.get("key") == ticket_key: t["pr_url"] = pr_url
        _save("tickets", s)


# ── In-flight / WIP control (event idempotency + concurrency cap) ────
def mark_in_flight(ticket_id: str) -> None:
    with _LOCK:
        f = _load("in_flight"); f[ticket_id] = _now(); _save("in_flight", f)
def clear_in_flight(ticket_id: str) -> None:
    with _LOCK:
        f = _load("in_flight"); f.pop(ticket_id, None); _save("in_flight", f)
def is_in_flight(ticket_id: str) -> bool:
    return ticket_id in _load("in_flight")
def in_flight_count() -> int:
    return len(_load("in_flight"))


# ── Plans / PRs / CI (GitHub side) ───────────────────────────────────
def put_plan(ticket_id: str, plan: str) -> None:
    with _LOCK:
        s=_load("plans"); s[ticket_id]={"ticket_id": ticket_id, "plan": plan, "at": _now()}; _save("plans", s)
def get_plan(ticket_id: str) -> str:
    return (_load("plans").get(ticket_id) or {}).get("plan", "")
def get_pr_diff(pr_url: str) -> str:
    return _load("prs").get(pr_url, {}).get("diff", "(dev: no diff — wire GITHUB_MCP_URL)")
def get_ci_status(pr_url: str) -> str:
    """PROD: GitHub checks API. DEV: green (override via prs store)."""
    return _load("prs").get(pr_url, {}).get("ci_status", "green")
def merge_pr(pr_url: str) -> bool:
    """PROD: gh pr merge. DEV: mark merged."""
    with _LOCK:
        s=_load("prs"); rec=s.get(pr_url) or {"pr_url": pr_url}; rec["merged"]=True; rec["merged_at"]=_now(); s[pr_url]=rec; _save("prs", s)
    return True


# ── QA / test tickets + bugs (the QA engineer's lane) ────────────────
QA_BOT = "qa-bot"
_SEED_QA = [
    {"ticket_id": "qa1", "key": "QA-440", "url": "https://jira.example/QA-440",
     "title": "Regression: payments API error paths", "priority": "high",
     "status": "open", "assignee": QA_BOT, "discipline": "api", "labels": ["qa"],
     "acceptance_criteria": ["4xx on bad input", "401 without auth", "429 over the rate limit"]},
    {"ticket_id": "qa2", "key": "QA-455", "url": "https://jira.example/QA-455",
     "title": "Smoke: checkout flow in the web app", "priority": "medium",
     "status": "open", "assignee": QA_BOT, "discipline": "frontend", "labels": ["qa"],
     "acceptance_criteria": ["add to cart", "apply coupon", "complete payment", "no console errors"]},
    {"ticket_id": "qa3", "key": "QA-460", "url": "https://jira.example/QA-460",
     "title": "Integration: order service against the new schema", "priority": "medium",
     "status": "open", "assignee": QA_BOT, "discipline": "backend", "labels": ["qa"],
     "acceptance_criteria": ["migrations apply cleanly", "suite green", "no N+1 on order fetch"]},
]


def _qa_tickets() -> dict[str, Any]:
    s = _load("qa_tickets")
    if not s:
        s = {t["ticket_id"]: t for t in _SEED_QA}; _save("qa_tickets", s)
    return s


def qa_assigned(discipline: str = "", assignee: str = QA_BOT,
                exclude_in_flight: bool = True) -> list[dict[str, Any]]:
    """Open QA/test tickets for the squad, optionally filtered by discipline,
    priority-ordered. PROD: Jira search (assignee + label=qa)."""
    rows = [t for t in _qa_tickets().values()
            if t.get("assignee") == assignee and t.get("status") == "open"]
    if discipline:
        rows = [t for t in rows if (t.get("discipline") or "backend") == discipline]
    if exclude_in_flight:
        flying = set(_load("qa_in_flight").keys())
        rows = [t for t in rows if t["ticket_id"] not in flying]
    rows.sort(key=lambda t: _RANK.get(t.get("priority", "low"), 3))
    return rows


def qa_mark_in_flight(ticket_id: str) -> None:
    with _LOCK:
        f = _load("qa_in_flight"); f[ticket_id] = _now(); _save("qa_in_flight", f)
def qa_clear_in_flight(ticket_id: str) -> None:
    with _LOCK:
        f = _load("qa_in_flight"); f.pop(ticket_id, None); _save("qa_in_flight", f)


def create_bug(summary: str, description: str = "", severity: str = "major",
               discipline: str = "backend", parent_key: str = "",
               reporter: str = "qa_agent") -> str:
    """File a bug back into Jira. DEV: persist + return a BUG-key.
    PROD: Jira create-issue (type=Bug, linked to parent_key)."""
    with _LOCK:
        seq = _load("bug_seq") or {}
        n = int(seq.get("n", 4000)) + 1
        seq["n"] = n; _save("bug_seq", seq)
        key = f"BUG-{n}"
        bugs = _load("bugs")
        bugs[key] = {"key": key, "summary": summary, "description": description,
                     "severity": severity, "discipline": discipline,
                     "parent_key": parent_key, "reporter": reporter,
                     "status": "open", "created_at": _now()}
        _save("bugs", bugs)
    return key


def get_bugs() -> dict[str, Any]:
    return _load("bugs")
