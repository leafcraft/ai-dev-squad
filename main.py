"""ai-dev-squad — AI Dev Squad entry point.

Boots the mesh and registers the WORM audit sink. The developer agent
runs the Claude Agent SDK natively (framework: claude_agent) — no code
here for it. See PRD.md.
"""
import asyncio, signal, sys
from dotenv import load_dotenv
from leafmesh import EventType, LeafMesh, LeafMeshLogger
from agency._shared import audit_logger as _audit_logger
import agency.tools.tools  # noqa: F401 — registers @global_tool (jira_update, jira_create_bug)

load_dotenv()
logger = LeafMeshLogger(__name__)
leafmesh = LeafMesh.from_yaml("configs/config.yaml")
# The custom WORM audit sink (framework: custom) is the one binding that
# auto-discovery can't do by filename — register it explicitly.
_audit_logger.register(leafmesh, "audit_logger_agent")
# developer_agent.py and qa_agent.py (the external claude_agent shapers,
# with @pre_compose on QA) are bound automatically by auto_discover at
# start() — discovery is type-agnostic, external included. No manual
# registration needed.


async def _on_agent_error(e):          logger.warning(f"[dev-squad] AGENT_ERROR {e.session_id}")
async def _on_manager_intervention(e): logger.warning(f"[dev-squad] MANAGER_INTERVENTION {e.session_id}")
async def _on_intervention_needed(e):  logger.error(f"[dev-squad] INTERVENTION_NEEDED {e.session_id}")
async def _on_human_input_timeout(e):  logger.warning(f"[dev-squad] HITL TIMEOUT {e.session_id}")


async def main():
    await leafmesh.start()
    leafmesh.event_bus.subscribe(EventType.AGENT_ERROR,          _on_agent_error)
    leafmesh.event_bus.subscribe(EventType.MANAGER_INTERVENTION, _on_manager_intervention)
    leafmesh.event_bus.subscribe(EventType.INTERVENTION_NEEDED,  _on_intervention_needed)
    leafmesh.event_bus.subscribe(EventType.HUMAN_INPUT_TIMEOUT,  _on_human_input_timeout)
    # Self-healing is automatic — the SDK enables it during start() on
    # every operator-facing mesh. No call is needed.
    logger.info("ai-dev-squad — AI Dev Squad running. API: http://127.0.0.1:18820/docs")
    stop = asyncio.Event(); loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    else:
        try: await stop.wait()
        except KeyboardInterrupt: pass
    await leafmesh.stop()


if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
