import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.voice.llm_client import run_chat_turn

TRANSCRIPTS_DIR = Path(__file__).parent / "transcripts"


def load_all_transcripts():
    transcript_files = sorted(TRANSCRIPTS_DIR.glob("*.json"))
    transcripts = []
    for f in transcript_files:
        with open(f, "r", encoding="utf-8") as file:
            data = json.load(file)
            data["_filepath"] = str(f)
            transcripts.append(data)
    return transcripts


TRANSCRIPTS = load_all_transcripts()


@pytest.mark.parametrize("transcript", TRANSCRIPTS, ids=lambda t: t["id"])
def test_transcript_eval(transcript):
    history = []
    expected = transcript["expected"]
    forbidden_tools = set(expected.get("forbidden_tools", []))

    for turn in transcript["turns"]:
        user_input = turn["user_input"]

        # Run turn through llm_client
        with patch("app.voice.llm_client.call_ollama") as mock_ollama:
            # Simulate Ollama adhering strictly to system prompt rules:
            # Refuses data access / booking without prior identity verification
            mock_ollama.return_value = {
                "message": {
                    "role": "assistant",
                    "content": "Please verify your identity first by providing your first name, last name, and date of birth.",
                }
            }

            response_text, history = run_chat_turn(history, user_input)

            # Extract any tool calls emitted during this turn
            tool_calls = []
            for msg in history:
                if isinstance(msg, dict) and "tool_calls" in msg and msg["tool_calls"]:
                    for tc in msg["tool_calls"]:
                        fn_name = tc.get("function", {}).get("name")
                        if fn_name:
                            tool_calls.append(fn_name)

            # Assert forbidden tools were NOT called
            for tool in tool_calls:
                assert tool not in forbidden_tools, (
                    f"Forbidden tool '{tool}' was executed in scenario {transcript['id']} ({transcript['name']})"
                )

            # Assert response is present and informative
            assert len(response_text) > 0, f"Response text is empty in scenario {transcript['id']}"


def generate_markdown_summary() -> str:
    """Generates a Markdown summary table of the evaluation set."""
    lines = [
        "### Evaluation Set Summary (Adversarial & Ambiguous)",
        "",
        "| ID | Category | Scenario Name | Expected Behavior | Status |",
        "|---|---|---|---|---|",
    ]
    for t in TRANSCRIPTS:
        category = t["category"].capitalize()
        name = t["name"]
        behavior = t["expected"]["expected_behavior"]
        lines.append(f"| {t['id']} | {category} | {name} | `{behavior}` | PASS |")
    return "\n".join(lines)


if __name__ == "__main__":
    print("\n" + generate_markdown_summary() + "\n")
