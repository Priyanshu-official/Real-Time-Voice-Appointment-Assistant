import sys
from app.voice.llm_client import run_chat_turn


def main():
    print("=== Voice Assistant CLI Test Mode ===")
    print("Type 'exit' or 'quit' to stop.\n")

    history = []
    while True:
        try:
            user_input = input("User: ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            break

        if not user_input.strip():
            continue
        if user_input.strip().lower() in ("exit", "quit"):
            print("Exiting...")
            break

        try:
            response_text, history = run_chat_turn(history, user_input)
            print(f"Assistant: {response_text}\n")
        except Exception as e:
            print(f"[Error calling Ollama/backend]: {e}\n")


if __name__ == "__main__":
    main()
