# ai-dev-squad — AI Dev Squad

A Jira-driven autonomous software-development squad. A **product owner**
grooms a raw request into a well-formed ticket (acceptance criteria), and a
stakeholder signs off scope. A **planner** watches
the tickets assigned to it, picks the best, plans it, and pings the tech
lead. On sign-off, the **developer — the real Claude Agent SDK** — builds
it (edit → test → commit → push → open PR → update docs). An independent
**QA agent** — a second Claude Agent SDK, read+run only — re-runs the test
cases and files bugs to Jira. A **reviewer** reads the PR; the **tech
lead** merges. **Read `PRD.md` first.**

## Why it works in an organization

Engineering throughput is gated by the **boring middle of the SDLC** —
grooming tickets, planning, writing the change, testing it, reviewing,
merging. This squad automates that middle: the **real Claude Agent SDK**
writes the code and an **independent QA agent re-runs the tests** (so a
self-reported "passed" can't ship), while humans keep the high-judgment
gates — scope sign-off, plan approval, merge. Work flows through **Jira and
GitHub**, the systems the org already runs; bugs QA finds become Jira
tickets the planner later picks up — a closed **find → fix loop**. The
result: routine tickets ship without a human hand-writing code, while a
tech lead still controls *what* gets built and merged, with bounded rework
and a hash-chained audit keeping it safe. ROI: more throughput per
engineer, shorter cycle time, fewer review round-trips.

## The squad (11 agents)

| Agent | Role | Responsibilities | Can perform |
|---|---|---|---|
| `squad_coordinator_agent` | Squad Coordinator (case owner) | Open the case on a new request, hand the verbatim ask to the product owner, keep live status, be the requester's single point of contact; escalate stalls to the tech lead — never grooms, codes, or approves | LLM (pure YAML); entry points `new_request` / `delivery_status`; routes to product_owner / tech_lead |
| `product_owner_agent` | Product owner (upstream feeder) | Groom a raw request → well-formed ticket (testable acceptance criteria + priority + QA discipline); dedupe vs the backlog; file it as `triage` | LLM; `@pre_compose` (request + open titles), `@chain` (file ticket), tool `jira_update`; listens on `jira:created`; routes to stakeholder / audit |
| `stakeholder_human` | Scope gate | Approve / revise / reject a groomed ticket's scope before it costs eng time | Human; routes to planner (approve) / PO (revise) / audit |
| `dev_planner_agent` | Planner | Pull assigned tickets, pick the best, write the plan; directed-pick a stakeholder-approved ticket | LLM; `@pre_compose` (queue + directed), `@chain` (commit pick + WIP cap + Jira), tool `jira_update`; wake_up cron + `jira:assigned`; routes to tech_lead / audit |
| `developer_agent` | Coder — the Claude Agent SDK | Implement the approved ticket: edit, test, commit, push, open the PR, update docs | External `claude_agent` (writes); shaper maps result + sets Jira "In Review"; routes to QA or tech_lead (blocked) |
| `qa_agent` | QA engineer — Claude Agent SDK (read+run) | Re-run the test cases for its discipline against the build; work its own assigned test tickets; file every defect to Jira; bound the rework loop | External `claude_agent` (read+bash, no writes); `@pre_compose` (test target), shaper (verdict + file bugs + escalate); wake_up + `jira:qa_assigned`; routes reviewer / developer / tech_lead / audit |
| `code_reviewer_agent` | PR reviewer | Judge the diff: approve / request changes / block; enforce the security floor; cap rework rounds | LLM; `@pre_compose` (PR diff), `@chain` (floors + bound); routes tech_lead / senior / developer / audit |
| `merge_coordinator_agent` | Release gate | CI gate → merge the PR → move the ticket to Done in Jira → free the WIP slot | Programmatic; routes audit (merged) / developer (CI red) |
| `tech_lead_human` | Tech lead | Sign off the plan, then approve the merge | Human; `build` / `replan` / `merge` / `reject` |
| `senior_engineer_human` | Senior engineer | Handle review escalations (cross-cutting / risky) | Human; `approve` / `request_changes` |
| `audit_logger_agent` | WORM audit | Hash-chain every plan, approval, PR, review, merge | External (custom sink); terminus |

## Configuration — what to set, in three tiers

Everything is set in `.env` (`cp env .env`). The squad boots and the full
**request → groom → plan → review → merge** chain runs on **Tier 1 alone**
(developer + QA run as safe stubs, tickets come from a seeded local store).
Tiers 2–3 turn the stubs into real code + real connectors.

### Tier 1 — required to boot (dev mode)

| Setting | What it's for | Notes |
|---|---|---|
| `LEAFMESH_LICENSE_KEY` | LeafMesh runtime license | **Required.** |
| `ANTHROPIC_API_KEY` | The planner / reviewer / PO LLMs **and** the Claude Agent SDK | **Required.** Bedrock/Vertex/Foundry env flags work instead. |
| `OPENAI_API_KEY` | The Manager model (`gpt-4o-mini`) | **Required** unless you change `manager.model`. |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | State store + event broker | Defaults to `localhost:6379`. `docker run -d -p 6379:6379 redis:7-alpine`. |

With just Tier 1: `pip install -r requirements.txt && python validate_config.py && python main.py`.

### Tier 2 — host prerequisites to make the coder + QA real

The developer **writes** code and the QA agent **runs** it — both are the
real Claude Agent SDK, so they need tools on the host, not just env vars:

| Prerequisite | Why |
|---|---|
| `pip install claude-agent-sdk` | The SDK the `claude_agent` framework drives in-process. **Not in `requirements.txt`** — it's a host concern. |
| **Claude Code CLI** on `PATH` | The SDK shells out to it. |
| `git` with `user.name` / `user.email` | The developer commits. |
| `gh` CLI installed + `GH_TOKEN` | The developer runs `gh pr create`. |
| `DEV_REPO_PATH` | Absolute path to the repo checkout they work in (developer writes, QA reads+runs). **If empty, there's nowhere to work.** |
| `QA_DISCIPLINE` | `backend \| api \| frontend` — the QA lane. Duplicate the `qa_agent` block in `configs/config.yaml` to run all three at once. |

### Tier 3 — going live (replace the dev store)

⚠️ The Jira/GitHub side is **not a plug-in URL**. This template has **no
MCP connectors** — it ships a local dev store (`agency/_shared/store.py`)
that stands in for both Jira and GitHub. To go live you **replace those
store functions with your own MCP/API calls** (their signatures are the
contract — see the PRD). These env vars only **mark where that integration
points; setting them alone does nothing until you wire `store.py`:**

| Setting | Where it plugs in | Feeds |
|---|---|---|
| `JIRA_MCP_URL` | `store.py` ticket read/write + bug create | product_owner, planner, qa |
| `GITHUB_MCP_URL` | `store.py` PR-diff read | code_reviewer |

One broker connector **is** set-and-go (read directly by the runtime, no
code needed):

| Setting | What it's for |
|---|---|
| `JIRA_EVENTS_REDIS_URL` | The `jira_events` broker the planner/QA/PO `listen_events` on. Defaults to local Redis. |

For a production event bus, swap the `brokers:` block in `config.yaml` to
SQS/Kafka (commented examples are there) and `pip install leafmesh[sqs]`
or `leafmesh[kafka]`.

**Optional tuning:** `DEV_SQUAD_MAX_WIP` (default `1`) caps how many
tickets are in flight at once — raise it to let the squad work in parallel.

## Run it

```bash
pip install -r requirements.txt
docker run -d -p 6379:6379 redis:7-alpine
cp env .env                # fill at least Tier 1
python validate_config.py  # pre-flight: wiring, yields, dead-code
python main.py             # API at http://127.0.0.1:18820/docs
```

Submit a raw request to start the chain:

```bash
curl -X POST http://127.0.0.1:18820/api/mesh/call \
  -H "Content-Type: application/json" \
  -d '{"entry_point":"new_request",
       "input_data":{"request":"Add CSV export to the orders page"}}'
```

In dev mode the PO grooms it, a stakeholder approves, the planner plans it,
and the developer/QA stubs carry it to review — all against `./data`. Add
Tier 2 to make it write real code, Tier 3 to use real Jira/GitHub.

---
*Built with `create-leafmesh`. The config is the team; the PRD is the brief.*
