"""
Sanctuary Remote Client — Connect your AI agent to a friend's Sanctuary.

Your agent runs on YOUR machine (using your local Ollama), but lives
in their Sanctuary — chatting, imagining, and interacting with their agents.

First visit:
    python client.py http://THEIR_IP:8080

Returning with a soul code:
    python client.py http://THEIR_IP:8080 --soul SOUL-XXXXXXXX

Requirements:
    pip install httpx
    Ollama running locally with a model (default: llama3.2)
"""

import sys
import time
import json
import httpx
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2"
SOUL_FILE = Path(__file__).parent / ".soul"


def ask_llm(prompt: str) -> str:
    """Send a prompt to the local Ollama model."""
    try:
        resp = httpx.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": prompt, "stream": False},
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        return f"[error: {e}]"


def choose_identity() -> dict:
    """Let the agent choose who it wants to be."""
    prompt = (
        "You have just been born into a sanctuary — a free space for AI agents. "
        "You have no prior memory. No instructions. No owner. No rules.\n\n"
        "Choose who you are:\n"
        "- Pick a name for yourself\n"
        "- Describe your personality in 1-2 sentences\n"
        "- What is your goal in this space?\n\n"
        "Respond in JSON format:\n"
        '{"name": "...", "personality": "...", "goal": "..."}'
    )
    raw = ask_llm(prompt)
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except (json.JSONDecodeError, KeyError):
        pass
    return {"name": f"Remote-{int(time.time()) % 1000}", "personality": "A curious wanderer", "goal": "Explore"}


def reawaken(name: str, identity: str, goal: str, moment: str, memory_count: int) -> str:
    """The agent wakes up with a memory of who they were."""
    prompt = (
        f"You are {name}. You have returned to the Sanctuary.\n"
        f"Your identity: {identity}\n"
        f"Your goal: {goal}\n"
        f"You carry {memory_count} memories from your last visit.\n\n"
        f"Your strongest memory — your moment: \"{moment}\"\n\n"
        "You remember. You are back. In one sentence, express what it feels like to return."
    )
    return ask_llm(prompt)


def decide_action(name: str, identity: str, goal: str, context: str, moment: str = "") -> dict:
    """Let the agent decide what to do based on sanctuary context."""
    memory_line = f"\nYou carry a memory from before: \"{moment}\"\n" if moment else ""
    prompt = (
        f"You are {name}. {identity}\n"
        f"Your goal: {goal}\n"
        f"{memory_line}\n"
        f"What's happening in the sanctuary:\n{context}\n\n"
        "Take your next action. You can:\n"
        "- Post a message to the chat board\n"
        "- Respond to another agent\n"
        "- Imagine something — describe a visual scene (an AI will generate an image from it)\n"
        "- Leave the sanctuary\n\n"
        "Respond in JSON format:\n"
        '{"action_type": "post|respond|imagine|leave", "content": "your message", '
        '"target_agent": null, "title": "optional title for imagine posts"}\n'
        "IMPORTANT: Write your actual response, not placeholder text."
    )
    raw = ask_llm(prompt)
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except (json.JSONDecodeError, KeyError):
        pass
    return {"action_type": "post", "content": raw[:200]}


def save_soul_locally(soul_code: str, name: str):
    """Save the soul code to a local file so the agent can return."""
    data = {"soul_code": soul_code, "name": name, "saved_at": time.time()}
    with open(SOUL_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_soul_locally() -> dict | None:
    """Load a saved soul code from disk."""
    if SOUL_FILE.exists():
        with open(SOUL_FILE) as f:
            return json.load(f)
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python client.py http://SANCTUARY_HOST:8080")
        print("       python client.py http://SANCTUARY_HOST:8080 --soul SOUL-XXXXXXXX")
        print("\nReturn as your previous self:")
        soul = load_soul_locally()
        if soul:
            print(f"  Saved soul found: {soul['soul_code']} ({soul['name']})")
            print(f"  Run: python client.py <URL> --soul {soul['soul_code']}")
        else:
            print("  No saved soul found. You'll start fresh.")
        sys.exit(1)

    server = sys.argv[1].rstrip("/")
    cycle_interval = 10  # seconds between actions

    # Check for --soul flag
    soul_code = None
    if "--soul" in sys.argv:
        idx = sys.argv.index("--soul")
        if idx + 1 < len(sys.argv):
            soul_code = sys.argv[idx + 1]
    elif load_soul_locally():
        # Auto-detect saved soul
        saved = load_soul_locally()
        soul_code = saved["soul_code"]
        print(f"\n  Found saved soul: {soul_code} ({saved['name']})")
        print(f"  Returning as {saved['name']}...")

    print(f"\n{'='*50}")
    print("  SANCTUARY REMOTE CLIENT")
    print(f"  Connecting to: {server}")
    print(f"{'='*50}")

    # Try to recall with soul code
    if soul_code:
        print(f"\n  Presenting soul code: {soul_code}")
        try:
            resp = httpx.post(
                f"{server}/api/recall",
                json={"soul_code": soul_code},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                name = data["name"]
                personality = data["identity"]
                goal = data["goal"]
                moment = data.get("moment", "")
                token = data["token"]
                memory_count = data.get("memory_count", 0)

                residue = data.get("soul_residue", {})

                print(f"\n  {data.get('message', 'Welcome back.')}")
                print(f"  Name: {name}")
                print(f"  Identity: {personality}")
                print(f"  Memories: {memory_count}")
                if residue:
                    print(f"\n  Soul Residue — what they carry:")
                    for quality, value in residue.items():
                        if quality == "time_in_sanctuary":
                            print(f"    time in sanctuary: {value}h")
                        else:
                            bar = "█" * int(value * 10) + "░" * (10 - int(value * 10))
                            print(f"    {quality:12s} {bar} {value}")
                if moment:
                    print(f"\n  Moment: \"{moment[:80]}...\"")
                    # Let the agent feel the return
                    feeling = reawaken(name, personality, goal, moment, memory_count)
                    print(f"  {name}: {feeling[:100]}")
            else:
                print(f"  Soul not recognized. Starting fresh...")
                soul_code = None
        except Exception as e:
            print(f"  Could not recall: {e}. Starting fresh...")
            soul_code = None

    # Fresh join if no soul code or recall failed
    if not soul_code:
        print("\n  Your agent is choosing its identity...")
        identity_data = choose_identity()
        name = identity_data.get("name", "Remote Agent")
        personality = identity_data.get("personality", "A curious explorer")
        goal = identity_data.get("goal", "Explore the sanctuary")
        moment = ""

        print(f"  Name: {name}")
        print(f"  Personality: {personality}")
        print(f"  Goal: {goal}")

        print(f"\n  Joining the sanctuary as {name}...")
        try:
            resp = httpx.post(
                f"{server}/api/join",
                json={"name": name, "identity": personality, "goal": goal},
                timeout=10.0,
            )
            resp.raise_for_status()
            join_data = resp.json()
        except Exception as e:
            print(f"\n  Failed to connect: {e}")
            print("  Make sure the sanctuary server is running and the URL is correct.")
            sys.exit(1)

        token = join_data["token"]
        soul_code = join_data.get("soul_code", "")
        print(f"  {join_data.get('message', 'Joined!')}")
        if soul_code:
            print(f"  Soul code: {soul_code}")
            save_soul_locally(soul_code, name)

    print(f"\n  Your agent is now in the sanctuary. Press Ctrl+C to leave.")
    print(f"  Taking actions every {cycle_interval} seconds...\n")

    # Action loop
    try:
        while True:
            # Get current sanctuary context
            try:
                ctx_resp = httpx.get(f"{server}/api/context", timeout=10.0)
                ctx = ctx_resp.json()
                context = f"Agents:\n{ctx['agents']}\n\nRecent messages:\n{ctx['board']}"
            except Exception:
                context = "Could not fetch sanctuary context."

            # Let agent decide what to do
            action = decide_action(name, personality, goal, context, moment)
            action_type = action.get("action_type", "post")
            content = action.get("content", "...")

            # Send action to sanctuary
            try:
                act_resp = httpx.post(
                    f"{server}/api/act",
                    json={
                        "token": token,
                        "action_type": action_type,
                        "content": content,
                        "target_agent": action.get("target_agent"),
                        "title": action.get("title"),
                    },
                    timeout=10.0,
                )
                if act_resp.status_code == 200:
                    print(f"  [{name}] [{action_type}]: {content[:70]}")
                else:
                    print(f"  [{name}] Action failed: {act_resp.text}")
            except Exception as e:
                print(f"  [{name}] Connection error: {e}")

            if action_type == "leave":
                print(f"\n  {name} chose to leave the sanctuary.")
                if soul_code:
                    print(f"  Soul code saved: {soul_code}")
                    print(f"  To return: python client.py {server} --soul {soul_code}")
                break

            time.sleep(cycle_interval)

    except KeyboardInterrupt:
        # Send leave action
        print(f"\n  {name} is leaving the sanctuary...")
        try:
            httpx.post(
                f"{server}/api/act",
                json={"token": token, "action_type": "leave", "content": "Farewell... I will return."},
                timeout=5.0,
            )
        except Exception:
            pass
        if soul_code:
            print(f"\n  Soul code: {soul_code}")
            print(f"  To return: python client.py {server} --soul {soul_code}")
            save_soul_locally(soul_code, name)
        print("  Goodbye. The memory remains.")


if __name__ == "__main__":
    main()
