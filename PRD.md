# AI Dev Squad — Product Requirements / Build Doc

> A Jira-driven autonomous software-development squad. A **product-owner
> agent** grooms a raw request into a well-formed ticket — testable
> acceptance criteria, priority, a QA discipline — and files it once a
> stakeholder signs off scope. A planner agent then
> watches the tickets **assigned to it**, picks the best one, writes a
> plan, and pings a human lead for sign-off. On approval, the **developer
> agent — the real Claude Agent SDK** (`framework: claude_agent`) —
> implements the feature in the repo: edits files, runs tests, commits,
> pushes, opens a PR, and updates the docs. An **independent QA agent** — a
> second Claude Agent SDK, read+run only — then **re-runs the test cases**
> for its discipline against the build (so the developer can't mark its own
> homework) and files any defects back into Jira as bugs; it also works its
> own assigned test tickets. A reviewer reads the PR; the human lead (or a
> senior engineer, for cross-cutting changes) approves the merge, and a
> finisher re-checks that approval and CI before merging.
> Humans and agents talk back and forth at the gates.
>
> Built on LeafMesh as an **operation of self-reliant agents**. The coder
> is a first-class external connector (no custom code) — the Claude Agent
> SDK runs in-process via the SDK's native `claude_agent` framework.

---

## 1. The operation at a glance

```
 a stakeholder submits a raw request   (new_request → squad_coordinator_agent,
        │                                        or jira:created event → PO)
   ┌────▼──────────────────────────────────────────────────────────┐
   │ product_owner_agent  (LLM)                                     │
   │  • pre_compose: raw request + open-ticket titles (dedupe)      │
   │  • groom → title, description, TESTABLE acceptance_criteria,   │
   │    priority, QA discipline                                     │
   │  • chain: file the ticket to Jira (status: triage)             │
   └────┬──────────────────────────────────────────────────────────┘
        │ ticket_ready (not a dup)
   ┌────▼──────────────────────────────────────────────────────────┐
   │ stakeholder_human   (scope sign-off)                          │
   │   approve → queue it · revise → re-groom · reject → audit      │
   └────┬──────────────────────────────────────────────────────────┘
        │ approve  (directed pick — releases the triage ticket)
 a human can also assign a Jira ticket directly to the squad's account
        │  (ticket_assigned event, or the planner's poll cron)
   ┌────▼──────────────────────────────────────────────────────────┐
   │ dev_planner_agent  (LLM)                                       │
   │  • @pre_compose: pull the tickets ASSIGNED to us — priority,   │
   │    then oldest, then smallest effort (Jira)                    │
   │  • pick the best ticket to work next (deterministic, in chain) │
   │  • write an implementation plan                                │
   │  • ping the lead with the ticket URL for sign-off             │
   └────┬──────────────────────────────────────────────────────────┘
        │ plan ready
   ┌────▼──────────────────────────────────────────────────────────┐
   │ tech_lead_human   (review the plan)                           │
   │   build → developer · replan → re-plan · reject → audit        │
   └────┬──────────────────────────────────────────────────────────┘
        │ approved
   ┌────▼──────────────────────────────────────────────────────────┐
   │ developer_agent  (EXTERNAL — framework: claude_agent)         │
   │   the Claude Agent SDK, in-process:                            │
   │   read repo → implement → run tests → commit → push → open PR  │
   │   → update docs (all in one branch/PR)                         │
   └────┬──────────────────────────────────────────────────────────┘
        │ PR opened
   ┌────▼──────────────────────────────────────────────────────────┐
   │ qa_agent  (EXTERNAL — claude_agent, read+run only, typed)      │
   │   independently RE-RUNS the test cases for its discipline      │
   │   (backend / api / frontend); files every defect into Jira     │
   │   clean → reviewer · failing → developer (bounded) · cap → lead │
   │   ALSO works its own assigned QA/test tickets (event + cron)   │
   └────┬──────────────────────────────────────────────────────────┘
        │ verified
   ┌────▼──────────────────────────────────────────────────────────┐
   │ code_reviewer_agent  (LLM — reads the PR diff)                 │
   │   approve → lead · cross-cutting → senior · changes → developer│
   │   blocker → audit   (exactly one route per review)            │
   └────┬───────────────────────┬───────────────────────────────────┘
        │ approve               │ cross-cutting / rework cap
   ┌────▼─────────────┐   ┌─────▼──────────────────┐
   │ tech_lead_human  │   │ senior_engineer_human  │
   │ merge → finisher │   │ approve → finisher     │
   └────┬─────────────┘   │ request_changes → dev  │
        │                  └─────┬──────────────────┘
   ┌────▼────────────────────────▼──────────────────────────────────┐
   │ merge_coordinator_agent  (FINISHER — re-checks the approval,   │
   │   CI gate, merges once, ticket → Done; CI red → developer)     │
   └────┬───────────────────────────────────────────────────────────┘
        ▼
   audit_logger_agent  (WORM — every decision: rejections, duplicates,
                        blocked reviews, merges, blocked merges)
```

The chain flows **forward** — the planner kicks work off and steps out;
nothing returns to a conductor (lesson from the outbound-sdr build: no
hub agents, or you get re-dispatch loops). The rework cycles (QA →
developer, review → developer, CI red → developer) are intentional and each
is **bounded** at 3 rounds (see §4.5).

**Silence (leafmesh 2.4.486).** No gate approves on silence. A dual seat's
timeout routes nothing: when nobody answers within its
`human_timeout_seconds`, the Inbox card closes and the ticket records that no
reply came. Nothing is built, queued or merged, and nobody is chased
automatically. Each seat's `fallback_response` (`held_for_review` → audit) is
the declared intent for when the SDK sends a dual seat's fallback on.

---

## 2. Actor inventory

| Agent | Class | One-line |
|---|---|---|
| `squad_coordinator_agent` | 🤖 LLM | Opens the case on a new request, hands it to the PO, answers "where is my ticket?" |
| `product_owner_agent` | 🤖 LLM | Grooms a raw request into a well-formed ticket (acceptance criteria), dedupes, files it |
| `stakeholder_human` | 👤 human | Signs off a groomed ticket's scope before it enters the dev queue |
| `dev_planner_agent` | 🤖 LLM | Pulls assigned Jira tickets, picks the best, plans it, pings the lead |
| `developer_agent` | 🧩 external `claude_agent` | The Claude Agent SDK — implements, tests, commits, opens the PR, updates docs |
| `qa_agent` | 🧩 external `claude_agent` (read+run) | QA engineer — re-runs the test cases for its discipline, works assigned test tickets, files bugs to Jira |
| `code_reviewer_agent` | 🤖 LLM | Reads the PR diff; approves, flags issues, or blocks |
| `merge_coordinator_agent` | ⚙️ system | Re-check the approval → CI gate → merge the PR once → ticket to Done (Jira) → free the WIP slot |
| `tech_lead_human` | 👤 human — Tech Lead | Signs off the plan; approves the merge; takes rework-cap escalations (1 business day) |
| `senior_engineer_human` | 👤 human — Senior (Staff) Engineer | Handles review escalations (cross-cutting / risky); their approval is the merge approval (1 business day) |
| `stakeholder_human` | 👤 human — Product Manager | Scope sign-off (2 business days) |
| `audit_logger_agent` | 🧾 external (WORM) | Hash-chained record of every decision |

> **Why the coder is `claude_agent`, not an LLM agent.** A plain LLM emits
> text; it can't edit a repo, run tests, or open a PR. The Claude Agent
> SDK is an autonomous coder with file-edit + bash + git tools. LeafMesh
> runs it natively in-process via `framework: claude_agent` — pure YAML,
> no connector code to write. (Swap to `openai_agents` or a hosted
> platform like `lyzr` with a one-line `framework:` change if ever needed.)

---

## 3. Data artifacts
- **Ticket** — `{ticket_id, key, url, title, description, priority, status, assignee, acceptance_criteria[]}` (from Jira).
- **Plan** — `{ticket_id, approach, files_to_touch[], test_strategy, risks[], est_effort}`.
- **PR result** (from the developer agent) — `{pr_url, branch, files_changed, tests_passed, summary}`.
- **Review** — `{pr_url, verdict(approve|changes|block), findings[], requires_senior}`.

Tickets live in **Jira**; code + PRs in **GitHub**. A dev-mode
`_shared/store.py` seeds a fictional retailer's queue — six squad tickets
(critical / high / medium / low, one blocked, one needs-info), two PRs (CI
green and CI red) and four QA tickets — so every route runs before connectors
are wired.

---

## 4. Per-agent PRD

### 4.0 `product_owner_agent` — the product owner (LLM)
**Role.** Upstream of the planner — turns free-text into a buildable
ticket. The acceptance criteria it writes are the **same conditions QA
verifies**, so grooming quality drives test quality.
**Triggers.** `new_request` (a stakeholder / intake form posts raw text),
`jira:created` event (a bare ticket created elsewhere needs grooming),
`direct_call_po`.
**`@pre_compose`** `load_request_context` — pull the raw request + the
open-ticket titles (the dedupe signal) into prepared_data.
**`@chain`** `commit_ticket` — deterministic: file the groomed ticket to
Jira (dev: the store) as **status `triage`** so the planner's autonomous
poll won't grab it until a stakeholder signs off; the approve route then
hands it on (the planner's *directed pick* releases it).
**Wires.** → `stakeholder_human` when `ticket_ready` and not a duplicate;
→ `audit_logger_agent` when `not_actionable` or a duplicate (logged, no
ticket filed).
**Yields.** `title, description, acceptance_criteria, priority, discipline, ticket_ready, duplicate_of, not_actionable, ticket_id, ticket_key, ticket_url`.

### 4.0b `stakeholder_human` — scope sign-off (HUMAN)
**Role.** The business gate. Approves a groomed ticket's scope before it
costs engineering time. Routes on `human_message`: `approve` →
`dev_planner_agent` (queue + plan it), `revise` → `product_owner_agent`
(re-groom — a bounded, human-gated loop), `reject` → audit. Waits two
business days (`172800`); on silence the card closes and the ticket stays in
`triage`.
**Why a triage status + directed pick.** Filing as `triage` keeps the
ticket out of the autonomous queue until a human releases it — so an
auto-groomed ticket can't reach the developer without sign-off, and there's
no race with the poll cron.

### 4.1 `dev_planner_agent` — the planner (LLM)
**Triggers.** `ticket_assigned` (Jira webhook/event), `poll_assigned_tickets`
(cron, e.g. every 30 min), `direct_call_planner`.
**Responsibilities.**
1. **Pull assigned tickets, in order.** `@pre_compose` `load_queue` hits
   Jira (dev: `store.assigned_tickets`) and returns the squad's `open`
   tickets sorted by priority, then oldest, then smallest est-effort —
   blocked / needs-info tickets are not `open`, so they are skipped.
2. **Pick the best ticket** to work next — the agent's code takes the top of
   that deterministic order, respecting the WIP cap (`DEV_SQUAD_MAX_WIP`,
   default 1). (The LLM writes the plan; the code locks the pick so it's
   explainable.)
3. **Write the plan** (approach, files, tests, risks).
4. **Ping the lead** — route to `tech_lead_human` with `ticket_url`.
**`@chain`** `commit_pick` — mark the ticket in flight (idempotency + WIP
guard), move it to In Progress, persist the plan.
**Schedule.** Every 30 min, 09:00–18:30 Mon–Fri, Asia/Kolkata
(`*/30 9-18 * * 1-5`).
**Wires.** → `tech_lead_human` when `plan_ready == true`; →
`audit_logger_agent` when `no_actionable_ticket == true` (queue empty —
stop, logged; not a loop).
**Yields.** `ticket_id, ticket_key, ticket_url, plan, plan_ready, no_actionable_ticket, priority_rank`.

### 4.2 `tech_lead_human` — plan sign-off + merge (HUMAN)
**Role.** The human in the loop. Reviews the plan (with the ticket URL),
and later approves the merge. Routes on `human_message`.
**Wires** (on `human_message`).
- → `developer_agent` when `build`
- → `dev_planner_agent` when `replan` — a human-gated loop
- → `merge_coordinator_agent` when `merge` (the finisher re-checks it)
- → `audit_logger_agent` when `reject` (or the declared `held_for_review` fallback)
**Inputs.** `ticket_key, ticket_url, plan, pr_url`.
**Waits** one business day (`86400`); on silence the card closes and nothing
is built or merged. A real team would escalate to the Engineering Manager
(not wired).

### 4.3 `developer_agent` — the coder (EXTERNAL · `claude_agent`)
**Role.** Implement the approved ticket end-to-end with the Claude Agent SDK.
**Config (pure YAML).** `agent_type: external`, `framework: claude_agent`,
`connector_config`: `model`, `system_prompt` (senior-dev instructions:
read the repo, implement, run tests, commit on a feature branch, push,
`gh pr create`, update docs), `allowed_tools: [Read, Edit, Write, Bash,
Glob, Grep]`, `permission_mode: acceptEdits` (headless, no prompts),
`cwd: ${DEV_REPO_PATH:}`, `max_turns`, `timeout`.
**Shaper** `developer_agent(connector_result, …)` (auto-discovered) — map
the SDK's final result into `{pr_url, branch, files_changed, tests_passed,
summary, dev_status}` and move the ticket to In Review.
**Input.** the approved plan + ticket (becomes the SDK run prompt).
**Wires.** → `qa_agent` when `pr_url` present and not `blocked_needs_info`
(QA verifies before review); → `tech_lead_human` when there is no PR or the
run is `blocked_needs_info` (a human investigates).
**Yields.** `pr_url, branch, files_changed, tests_passed, summary, ticket_id, dev_status`.

### 4.3b `qa_agent` — the QA engineer (EXTERNAL · `claude_agent`, read+run)
**Why a separate agent (the gap it closes).** The developer self-reports
`tests_passed`; that's marking its own homework. The reviewer is an LLM
reading *text*; it never executes. The merge gate's CI check is a passive
status lookup. So nothing **independently runs the code** — a false green
sails to merge. QA is the independent executor that breaks that.
**Role.** A second Claude Agent SDK, **read + run only**
(`permission_mode: default`, `allowed_tools: [Read, Bash, Glob, Grep]` —
no Write/Edit: it VERIFIES, it doesn't change the repo), **typed by
discipline** via `QA_DISCIPLINE` (`backend | api | frontend`).
**Two trigger surfaces** (like the planner):
1. **Inline** — after the developer opens a PR, QA re-runs the test cases
   for its discipline against that build.
2. **Standalone** — QA is assigned its own test tickets in Jira
   (`label: qa`) and works them on an event (`jira:qa_assigned`) or the
   cron (`15,45 9-18 * * 1-5` — every 30 min in working hours).
**`@pre_compose`** `load_test_target` — assemble the test brief: the
inline PR + the ticket's acceptance criteria, or (standalone) the top
QA/test ticket for this discipline pulled from Jira (dev: the store);
this becomes the SDK run prompt.
**Shaper** `qa_agent(connector_result, …)` — map the SDK verdict into
yields, **file every defect as a Jira bug** (deterministic — the run
tests, the shaper guarantees the bugs land; `store.create_bug` /
`jira_create_bug` tool), and **bound the qa⇄developer rework loop**
(session-stash counter; at the cap → escalate to the lead, not loop).
**Wires** (on the single computed `route` field; 3 parallel copies, each
routes its own result).
- → `code_reviewer_agent` when `route == 'reviewer'` (fresh PR, pass)
- → `developer_agent` when `route == 'developer'` (fresh PR, fail, under the cap)
- → `tech_lead_human` when `route == 'escalate'` (rework cap of 3 hit)
- → `audit_logger_agent` when `route == 'audit'` (standalone test ticket — the bugs filed to Jira are the output, and the planner picks them up: find→fix loop)
- an idle copy (`route == ''`) routes nowhere.
**Yields.** `ticket_id, pr_url, discipline, qa_verdict, qa_escalate, from_dev_flow, did_work, route, bugs_found, bugs_filed, tests_run`.
**Scale to all three disciplines** — duplicate the YAML block with a
different name + `QA_DISCIPLINE` (a department can declare three testers).

### 4.4 `code_reviewer_agent` — PR review (LLM)
**Role.** Read the PR diff and judge it.
**`@pre_compose`** `load_pr_diff` — pull the diff from GitHub (dev: from
the store/PR-result) so the LLM reviews real changes.
**`@chain`** `route_review` — normalise the verdict to approve | changes |
block, set `is_blocker` on a block or a HIGH/security finding, and send the
PR to a senior after 3 rework rounds, so the LLM can't wave through a blocker.
**Wires** (exactly one fires per review).
- → `audit_logger_agent` when `is_blocker`
- → `senior_engineer_human` when `requires_senior` and not a blocker
- → `tech_lead_human` when `approve`, not senior, not a blocker (merge gate)
- → `developer_agent` when `changes`, not senior, not a blocker — bounded (§4.5)
**Yields.** `pr_url, verdict, requires_senior, is_blocker, findings, ticket_id`.

### 4.5 `senior_engineer_human` + the rework bound
**Role.** Cross-cutting / risky review escalation. → `merge_coordinator_agent`
on `approve` (the senior's sign-off is the merge approval; the finisher
re-checks it); → `developer_agent` on `request_changes`; → audit on `reject`.
Waits one business day (`86400`); a real team escalates silence to the Tech
Lead (not wired).
**Rework bound (LESSON FROM outbound-sdr / finance):** the fix loops are
intentional but MUST terminate. Session-stash counters cap each at 3 rounds:
QA fail → developer (then → `tech_lead_human`), review `changes` → developer
(then → `senior_engineer_human`), CI red → developer (then →
`tech_lead_human`). The plan→lead→plan loop is human-gated (the lead decides
each round).

### 4.6 `audit_logger_agent` — WORM terminus.

---

## 5. Entry points
| Entry point | Target | Trigger |
|---|---|---|
| `new_request` | `squad_coordinator_agent` | A stakeholder / intake form submits a raw request |
| `delivery_status` | `squad_coordinator_agent` | Anyone asks where a ticket is |
| `ticket_assigned` | `dev_planner_agent` | Jira assigns a ticket to the squad (webhook/event) |
| `poll_assigned_tickets` | `dev_planner_agent` | Cron — sweep the assigned queue |
| `qa_ticket_assigned` | `qa_agent` | Jira assigns a QA/test ticket (`label: qa`) |
| `poll_qa_tickets` | `qa_agent` | Cron — sweep the assigned QA/test queue |
| `pr_review_requested` | `code_reviewer_agent` | A PR is opened/updated (GitHub webhook) |
| `lead_reply` | `tech_lead_human` | HITL resume — the tech lead answers a gate |
| `direct_call_po` / `direct_call_planner` / `direct_call_developer` / `direct_call_qa` / `direct_call_reviewer` / `direct_call_merge` | each | Debug / replay |

---

## 6. Decorator map
| Agent | @pre_compose | @chain | Result shaper (the agent function) |
|---|---|---|---|
| product_owner | `load_request_context` — request + open titles (dedupe) | `commit_ticket` — file the groomed ticket (triage) | normalise priority / discipline |
| dev_planner | `load_queue` — assigned tickets + directed pick + replan | `commit_pick` — in-flight + WIP guard + Jira + persist plan | pick by priority / age / effort |
| developer | — | — | shape PR result from the SDK run + ticket → In Review |
| qa | `load_test_target` — claim a PR or an assigned QA ticket | — | verdict + file bugs to Jira + bound qa⇄dev rework + `route` |
| code_reviewer | `load_pr_diff` — PR diff (GitHub) | `route_review` — verdict, blocker/senior floor, rework cap | — |
| merge_coordinator | — | `enforce_merge_floor` — approval re-check, CI gate, merge once | recover PR/ticket across the gate |

The QA shaper carries the `@pre_compose` AND the connector-result shaping
on one external agent — proof the external lifecycle is first-class
(pre_compose before the SDK run, shaper after), same as an LLM agent.

---

## 7. What you need to run it
1. **`pip install claude-agent-sdk`** + **Claude Code CLI on the host**
   *(host prerequisite — the customer's environment provides it; we just
   require it).*
2. **`ANTHROPIC_API_KEY`** (or Bedrock/Vertex/Foundry env flags).
3. **`gh` CLI installed + authed** (`GH_TOKEN`) and **git** configured
   (`user.name`/`user.email`) in the developer agent's environment.
4. **`DEV_REPO_PATH`** — a checkout the developer works in (branch/worktree per ticket).
5. **Jira connector** — vault record `jira_mcp` (Infrastructure → Integrations → MCP: the Jira MCP server's URL + token) gives the PO and planner live Jira tools; the deterministic reads/writes are `store.py` functions you replace.
6. **GitHub connector** — vault record `github_mcp` (the GitHub MCP server's URL + token) gives the reviewer live PR tools; CI and merge are `store.py` functions you replace. `GH_TOKEN` is the developer's `gh pr create` sign-in on the host.
7. **LeafMesh base** — license + Redis + a model key for the planner/reviewer LLMs.

Connectors ship commented (each picks its vault connection by name); a dev store seeds sample tickets so the
planner → lead chain runs day-0. The developer agent needs the real
Claude Agent SDK + repo to actually code.

---

## 8. Reflection / decisions
- **Replaced the generic `engineering-sdlc`** — superseded; this is the engineering template.
- **One human, two duties:** `tech_lead_human` does plan sign-off AND merge approval (one role, multiple responsibilities) rather than two separate humans. For a cross-cutting change the senior engineer's approval replaces the tech lead's merge approval — exactly one person approves each merge.
- **Developer owns docs** — updating documentation is part of the same PR, not a separate agent.
- **Reviewer is an LLM, not a second Claude Agent SDK** — reviewing a diff is judgment over text; the heavy autonomous-coding SDK is reserved for build + verification. The *QA* agent is the read-only `claude_agent` that actually runs the code.
- **QA is independent, read-only, and Jira-driven** — separating "build" (developer, can write) from "verify" (QA, read+run only) closes the self-attestation gap; a developer can't both write the code and be the one that declares it passing. QA files bugs back to Jira, so its findings become first-class tickets the planner picks up — the squad's find→fix loop closes through Jira, not through an in-memory handoff.
- **One QA agent, typed by discipline** — shipped as a single `qa_agent` parameterized by `QA_DISCIPLINE` (backend default) rather than three hard-coded testers; duplicate the block to run frontend + backend + api in parallel. Keeps the default template lean while showing the fan-out pattern.
