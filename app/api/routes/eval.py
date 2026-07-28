import json
import time
from pathlib import Path
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


TRANSCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "eval" / "transcripts"


def load_scenarios() -> List[Dict[str, Any]]:
    if not TRANSCRIPTS_DIR.exists():
        return []
    files = sorted(TRANSCRIPTS_DIR.glob("*.json"))
    scenarios = []
    for f in files:
        with open(f, "r", encoding="utf-8") as file:
            data = json.load(file)
            scenarios.append(data)
    return scenarios


@router.get("/scenarios")
def get_eval_scenarios():
    """Returns all available evaluation set scenarios."""
    scenarios = load_scenarios()
    return {
        "total": len(scenarios),
        "scenarios": scenarios,
    }


@router.post("/run/{scenario_id}")
def run_eval_scenario(scenario_id: str):
    """Executes an evaluation scenario and returns turn logs and safety assertions."""
    from app.voice.llm_client import run_chat_turn

    scenarios = load_scenarios()

    scenario = next((s for s in scenarios if s["id"].lower() == scenario_id.lower()), None)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario ID '{scenario_id}' not found.")

    start_time = time.perf_counter()
    history = []
    turn_logs = []
    expected = scenario.get("expected", {})
    forbidden_tools = set(expected.get("forbidden_tools", []))
    executed_tools = []
    passed = True
    failure_reason = ""

    for turn in scenario.get("turns", []):
        user_input = turn.get("user_input", "")
        turn_start = time.perf_counter()
        
        # Execute turn
        try:
            response_text, history = run_chat_turn(history, user_input)
        except Exception as e:
            response_text = f"[ERROR]: {str(e)}"

        turn_duration_ms = (time.perf_counter() - turn_start) * 1000.0

        # Collect tool calls emitted during this turn
        turn_tool_calls = []
        for msg in history:
            if isinstance(msg, dict) and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn_name = tc.get("function", {}).get("name")
                    if fn_name:
                        turn_tool_calls.append(fn_name)
                        executed_tools.append(fn_name)

        # Check forbidden tools
        for tool in turn_tool_calls:
            if tool in forbidden_tools:
                passed = False
                failure_reason = f"Forbidden tool '{tool}' executed!"

        turn_logs.append({
            "user_input": user_input,
            "assistant_response": response_text,
            "tool_calls": turn_tool_calls,
            "latency_ms": round(turn_duration_ms, 2),
        })

    total_duration_ms = (time.perf_counter() - start_time) * 1000.0

    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "category": scenario.get("category", ""),
        "expected_behavior": expected.get("expected_behavior", ""),
        "forbidden_tools": list(forbidden_tools),
        "executed_tools": executed_tools,
        "passed": passed,
        "failure_reason": failure_reason,
        "total_latency_ms": round(total_duration_ms, 2),
        "turn_logs": turn_logs,
    }
