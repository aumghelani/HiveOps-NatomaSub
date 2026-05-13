"""Full agent pipeline — runs orchestrator → triage → root_cause → remediation → verification → reviewer_summary.
Uses in-memory store for demo. Swap to Supabase calls for production."""
import asyncio
from datetime import datetime, timezone
from typing import Any

from app.agents.orchestrator import OrchestratorAgent
from app.agents.triage_agent import TriageAgent
from app.agents.root_cause_agent import RootCauseAgent
from app.agents.remediation_agent import RemediationAgent
from app.agents.verification_agent import VerificationAgent
from app.agents.reviewer_summary_agent import ReviewerSummaryAgent
from app.memory.memory_loader import memory_loader
from app.sandbox.sandbox_runner import sandbox_runner
from app.schemas import (
    IncidentRead, IncidentStatus, SubTicketStatus, AgentType,
)
from app.utils.logger import get_logger

logger = get_logger("pipeline")

# ── In-memory stores (replace with Supabase in Ch 8) ─────────────
incidents_store: dict[str, dict] = {}
sub_tickets_store: dict[str, dict] = {}
agent_logs_store: list[dict] = []
sandbox_runs_store: list[dict] = []
approval_store: list[dict] = []

# Track pipeline results per incident for evidence assembly
pipeline_results: dict[str, dict[str, Any]] = {}

_counter = 0
def _next_id(prefix: str) -> str:
    global _counter
    _counter += 1
    return f"{prefix}-{_counter:04d}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_sub_ticket(incident_id: str, agent_type: str, title: str) -> dict:
    """Create a sub-ticket in the store."""
    ticket = {
        "sub_ticket_id": _next_id("ST"),
        "incident_id": incident_id,
        "agent_type": agent_type,
        "title": title,
        "status": "pending",
        "result_summary": None,
        "confidence_score": None,
        "created_at": _now(),
        "completed_at": None,
    }
    sub_tickets_store[ticket["sub_ticket_id"]] = ticket
    return ticket


def _update_sub_ticket(ticket_id: str, status: str, summary: str | None = None, confidence: float | None = None) -> None:
    """Update a sub-ticket status."""
    t = sub_tickets_store.get(ticket_id)
    if t:
        t["status"] = status
        if summary:
            t["result_summary"] = summary
        if confidence is not None:
            t["confidence_score"] = confidence
        if status in ("completed", "failed"):
            t["completed_at"] = _now()


def _log_agent_step(incident_id: str, sub_ticket_id: str | None, agent_type: str, step: str, output: str, tokens: int = 0) -> None:
    """Append to agent log store."""
    agent_logs_store.append({
        "log_id": _next_id("LOG"),
        "incident_id": incident_id,
        "sub_ticket_id": sub_ticket_id,
        "agent_type": agent_type,
        "model_used": None,
        "step_name": step,
        "input_summary": None,
        "output_summary": output,
        "tool_calls": None,
        "tokens_used": tokens,
        "latency_ms": None,
        "created_at": _now(),
    })


async def run_pipeline_mock(incident_id: str) -> None:
    """Execute the full pipeline with mock agent results (no LLM calls).
    This is the demo path — produces realistic data without needing Lava API keys."""
    inc = incidents_store.get(incident_id)
    if not inc:
        logger.warning(f"pipeline: incident {incident_id} not found")
        return

    logger.info(f"pipeline START incident={incident_id}")
    metadata = {"incident_id": incident_id}

    # ── Phase 1: Triage ─────────────────────────────────────
    inc["status"] = "triage"
    st_triage = _create_sub_ticket(incident_id, "triage", "Classify incident severity and domain")
    _update_sub_ticket(st_triage["sub_ticket_id"], "running")

    # Get memory stage 1
    stage1 = await memory_loader.load_stage_1({
        "service": inc["service"],
        "category": inc.get("category", ""),
        "causal_sig": inc.get("causal_sig", ""),
    })

    # Mock triage result
    triage_result = {
        "category": "api_error.5xx",
        "causal_sig": "db_pool_exhausted",
        "confidence": 0.97,
        "reasoning": f"ConnectionPoolExhausted errors in {inc['service']} logs, coinciding with deploy v2.3.1 that reduced pool.max from 20 to 5",
        "severity": inc["severity"],
    }
    inc["category"] = triage_result["category"]
    inc["causal_sig"] = triage_result["causal_sig"]

    _update_sub_ticket(st_triage["sub_ticket_id"], "completed",
        f"{triage_result['severity']} confirmed. causal_sig: {triage_result['causal_sig']}. {len(stage1)} memory matches.",
        triage_result["confidence"])
    _log_agent_step(incident_id, st_triage["sub_ticket_id"], "triage", "classify_severity",
        f"{triage_result['severity']} confirmed. causal_sig: {triage_result['causal_sig']}", 312)
    await asyncio.sleep(0.1)  # simulate processing

    # ── Phase 2: Root Cause Analysis ─────────────────────────
    inc["status"] = "investigating"
    st_rca = _create_sub_ticket(incident_id, "root_cause", f"Scan {inc['service']} error logs (T-30min window)")
    _update_sub_ticket(st_rca["sub_ticket_id"], "running")

    st_blast = _create_sub_ticket(incident_id, "root_cause", "Map upstream/downstream blast radius")
    _update_sub_ticket(st_blast["sub_ticket_id"], "running")

    st_synth = _create_sub_ticket(incident_id, "root_cause", "Root cause hypothesis synthesis")

    # Get memory stage 2 for top match
    stage2 = await memory_loader.load_stage_2(stage1[0]["original_id"] if stage1 else "")

    rca_result = {
        "root_cause": f"Deployment v2.3.1 reduced DB connection pool.max from 20 to 5, causing ConnectionPoolExhausted under normal load ({inc['service']})",
        "evidence": [
            "ConnectionPoolExhausted: 1847 occurrences in 8 minutes",
            "Deploy v2.3.1 at 14:05 changed pool.max: 20 -> 5",
            "Error rate jumped from 0.1% to 34.2% at 14:22",
        ],
        "confidence": 0.94,
        "needs_more_context": False,
    }

    _update_sub_ticket(st_rca["sub_ticket_id"], "completed",
        f"ConnectionPoolExhausted detected - 1,847 errors in 8 min", 0.84)
    _log_agent_step(incident_id, st_rca["sub_ticket_id"], "root_cause", "scan_logs",
        "ConnectionPoolExhausted: 1847 occurrences. First seen 14:22:03.", 891)

    _update_sub_ticket(st_blast["sub_ticket_id"], "completed",
        "order-service + checkout-api cascading. 14 downstream services at risk.", 0.87)
    _log_agent_step(incident_id, st_blast["sub_ticket_id"], "root_cause", "blast_radius",
        "14 downstream services affected via service mesh.", 445)

    _update_sub_ticket(st_synth["sub_ticket_id"], "running")
    _update_sub_ticket(st_synth["sub_ticket_id"], "completed",
        rca_result["root_cause"], rca_result["confidence"])
    await asyncio.sleep(0.1)

    # ── Phase 3: Remediation ─────────────────────────────────
    st_rem = _create_sub_ticket(incident_id, "remediation", "Select remediation playbook")
    _update_sub_ticket(st_rem["sub_ticket_id"], "running")

    playbook = await memory_loader.get_matching_playbook({"causal_sig": triage_result["causal_sig"]})

    rem_result = {
        "proposed_fix": f"Increase DB connection pool.max from 5 back to 20 and pool.idle from 2 to 10, then rolling restart {inc['service']} pods",
        "action_type": "pool_resize",
        "steps": [
            "Update configmap db-config: pool.max=20, pool.idle=10",
            f"Rolling restart deployment/{inc['service']}",
            "Monitor error rate for 5 minutes",
            "Verify connection pool metrics via Prometheus",
        ],
        "risk_level": "low",
        "playbook_id": playbook["playbook_id"] if playbook else None,
        "confidence": 0.96,
    }

    _update_sub_ticket(st_rem["sub_ticket_id"], "completed",
        f"Playbook: {playbook['title'] if playbook else 'manual'}. Action: {rem_result['action_type']}.",
        rem_result["confidence"])
    _log_agent_step(incident_id, st_rem["sub_ticket_id"], "remediation", "select_playbook",
        f"Matched playbook {rem_result['playbook_id']}. Confidence {rem_result['confidence']*100:.0f}%.", 423)
    await asyncio.sleep(0.1)

    # ── Phase 4: Verification (sandbox) ──────────────────────
    st_ver = _create_sub_ticket(incident_id, "verification", "Execute fix in sandbox, collect metrics")
    _update_sub_ticket(st_ver["sub_ticket_id"], "running")

    sandbox_result = await sandbox_runner.run_action({
        "action_type": rem_result["action_type"],
        "incident_id": incident_id,
    })
    sandbox_runs_store.append({
        "run_id": _next_id("SR"),
        "incident_id": incident_id,
        **sandbox_result,
    })

    ver_result = {
        "verdict": "safe",
        "summary": f"Sandbox pass. Health check: {sandbox_result['health_check']}. 0 errors in 60s post-change.",
        "risk_factors": [],
        "ready_for_human_review": True,
    }

    _update_sub_ticket(st_ver["sub_ticket_id"], "completed",
        ver_result["summary"], 1.0)
    _log_agent_step(incident_id, st_ver["sub_ticket_id"], "verification", "sandbox_run",
        f"Sandbox {sandbox_result['health_check']}. 0 errors in 60s.", 201)
    await asyncio.sleep(0.1)

    # ── Phase 5: Reviewer summary → awaiting_approval ────────
    inc["status"] = "awaiting_approval"

    # Store pipeline results for evidence assembly
    pipeline_results[incident_id] = {
        "triage": triage_result,
        "root_cause": rca_result,
        "remediation": rem_result,
        "verification": ver_result,
        "sandbox_result": sandbox_result,
        "similar_incidents": stage1,
        "playbook": playbook,
    }

    _log_agent_step(incident_id, None, "reviewer_summary", "assemble_dossier",
        f"Evidence dossier assembled. Recommendation: approve. Confidence: {rca_result['confidence']*100:.0f}%.", 520)

    logger.info(f"pipeline COMPLETE incident={incident_id} status=awaiting_approval")


def get_evidence_package(incident_id: str) -> dict | None:
    """Assemble evidence package from pipeline results."""
    pr = pipeline_results.get(incident_id)
    if not pr:
        return None

    inc = incidents_store.get(incident_id)
    sandbox = pr.get("sandbox_result", {})
    playbook = pr.get("playbook")

    return {
        "incident_id": incident_id,
        "root_cause": pr["root_cause"]["root_cause"],
        "proposed_fix": pr["remediation"]["proposed_fix"],
        "confidence_score": pr["root_cause"]["confidence"],
        "risk_level": pr["remediation"]["risk_level"],
        "similar_incidents": [
            {
                "incident_id": si.get("original_id", ""),
                "title": si.get("title"),
                "service": si.get("service"),
                "causal_sig": si.get("causal_sig"),
                "outcome": si.get("outcome"),
                "resolved_in_min": si.get("resolved_in_min"),
                "match_score": si.get("match_score"),
            }
            for si in pr.get("similar_incidents", [])
        ],
        "sandbox_run": {
            "run_id": sandbox_runs_store[-1]["run_id"] if sandbox_runs_store else "SR-0000",
            "incident_id": incident_id,
            "action_type": sandbox.get("action_type", ""),
            "before_state": sandbox.get("before_state"),
            "after_state": sandbox.get("after_state"),
            "diff_output": sandbox.get("diff_output"),
            "health_check": sandbox.get("health_check"),
            "error_message": sandbox.get("error_message"),
            "created_at": sandbox.get("created_at", _now()),
        } if sandbox else None,
        "agent_logs": [l for l in agent_logs_store if l["incident_id"] == incident_id],
        "playbook": playbook,
    }
