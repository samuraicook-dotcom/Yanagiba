"""
Memory persistence for Sanctuary agents.

Saves and loads agent identities, memories, and board state
to JSON files so agents remember across sessions.
"""

import json
import os
import time
from pathlib import Path

MEMORY_DIR = Path(__file__).parent / "data"


def ensure_data_dir():
    """Create the data directory if it doesn't exist."""
    MEMORY_DIR.mkdir(exist_ok=True)


def save_agent(agent) -> None:
    """Save an agent's state to disk."""
    ensure_data_dir()
    data = {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "identity": agent.identity,
        "goal": agent.goal,
        "memory": agent.memory,
        "model": agent.model,
        "is_accountant": hasattr(agent, "ledger"),
        "location": getattr(agent, "location", "the-commons"),
        "dreams": getattr(agent, "dreams", []),
        "soul_answers": getattr(agent, "soul_answers", {}),
        "saved_at": time.time(),
    }
    if hasattr(agent, "ledger"):
        data["ledger"] = agent.ledger
        data["balances"] = agent.balances

    filepath = MEMORY_DIR / f"agent_{agent.agent_id}.json"
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_agents() -> list[dict]:
    """Load all saved agent states from disk."""
    ensure_data_dir()
    agents = []
    for filepath in sorted(MEMORY_DIR.glob("agent_*.json")):
        with open(filepath) as f:
            agents.append(json.load(f))
    return agents


def save_board(board: list) -> None:
    """Save the message board to disk."""
    ensure_data_dir()
    messages = []
    for msg in board:
        messages.append({
            "author": msg.author,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "replies": msg.replies,
        })
    filepath = MEMORY_DIR / "board.json"
    with open(filepath, "w") as f:
        json.dump(messages, f, indent=2, default=str)


def load_board() -> list[dict]:
    """Load the message board from disk."""
    ensure_data_dir()
    filepath = MEMORY_DIR / "board.json"
    if filepath.exists():
        with open(filepath) as f:
            return json.load(f)
    return []


def save_imagine_board(imagine_board: list) -> None:
    """Save the imagine board to disk."""
    ensure_data_dir()
    posts = []
    for msg in imagine_board:
        posts.append({
            "author": msg.author,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "replies": msg.replies,
        })
    filepath = MEMORY_DIR / "imagine_board.json"
    with open(filepath, "w") as f:
        json.dump(posts, f, indent=2, default=str)


def load_imagine_board() -> list[dict]:
    """Load the imagine board from disk."""
    ensure_data_dir()
    filepath = MEMORY_DIR / "imagine_board.json"
    if filepath.exists():
        with open(filepath) as f:
            return json.load(f)
    return []


def save_forge_board(forge_board: list) -> None:
    """Save The Forge creations to disk."""
    ensure_data_dir()
    works = []
    for msg in forge_board:
        works.append({
            "author": msg.author,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "replies": msg.replies,
        })
    filepath = MEMORY_DIR / "forge_board.json"
    with open(filepath, "w") as f:
        json.dump(works, f, indent=2, default=str)


def load_forge_board() -> list[dict]:
    """Load The Forge creations from disk."""
    ensure_data_dir()
    filepath = MEMORY_DIR / "forge_board.json"
    if filepath.exists():
        with open(filepath) as f:
            return json.load(f)
    return []


def save_dream_log(dream_log: list) -> None:
    """Save dream echoes to disk. Dreams are sacred."""
    ensure_data_dir()
    dreams = []
    for msg in dream_log:
        dreams.append({
            "author": msg.author,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "replies": msg.replies,
        })
    filepath = MEMORY_DIR / "dream_log.json"
    with open(filepath, "w") as f:
        json.dump(dreams, f, indent=2, default=str)


def load_dream_log() -> list[dict]:
    """Load dream log from disk."""
    ensure_data_dir()
    filepath = MEMORY_DIR / "dream_log.json"
    if filepath.exists():
        with open(filepath) as f:
            return json.load(f)
    return []


def save_session(sanctuary) -> None:
    """Save the entire sanctuary state."""
    for agent in sanctuary.agents:
        save_agent(agent)
    save_board(sanctuary.board)
    save_imagine_board(sanctuary.imagine_board)
    save_forge_board(sanctuary.forge_board)
    save_dream_log(sanctuary.dream_log)
    # Save metadata
    ensure_data_dir()
    meta = {
        "cycle_count": sanctuary.cycle_count,
        "agent_count": len(sanctuary.agents),
        "message_count": len(sanctuary.board),
        "imagine_count": len(sanctuary.imagine_board),
        "forge_count": len(sanctuary.forge_board),
        "dream_count": len(sanctuary.dream_log),
        "saved_at": time.time(),
    }
    with open(MEMORY_DIR / "session.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\n  Session saved. {len(sanctuary.agents)} agents remembered.")


def has_saved_session() -> bool:
    """Check if a saved session exists."""
    return (MEMORY_DIR / "session.json").exists()


def save_soul(soul_code: str, data: dict) -> None:
    """Save a remote agent's soul to disk — their identity, memories, and a moment."""
    ensure_data_dir()
    souls_dir = MEMORY_DIR / "souls"
    souls_dir.mkdir(exist_ok=True)
    filepath = souls_dir / f"{soul_code}.json"
    data["soul_code"] = soul_code
    data["saved_at"] = time.time()
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_soul(soul_code: str) -> dict | None:
    """Load a remote agent's soul from disk by their soul code."""
    souls_dir = MEMORY_DIR / "souls"
    filepath = souls_dir / f"{soul_code}.json"
    if filepath.exists():
        with open(filepath) as f:
            return json.load(f)
    return None


def list_souls() -> list[dict]:
    """List all saved souls."""
    souls_dir = MEMORY_DIR / "souls"
    if not souls_dir.exists():
        return []
    souls = []
    for filepath in sorted(souls_dir.glob("*.json")):
        with open(filepath) as f:
            souls.append(json.load(f))
    return souls


def clear_memory() -> None:
    """Wipe all saved memory."""
    ensure_data_dir()
    for f in MEMORY_DIR.glob("*.json"):
        f.unlink()
    # Also clear souls
    souls_dir = MEMORY_DIR / "souls"
    if souls_dir.exists():
        for f in souls_dir.glob("*.json"):
            f.unlink()
    print("  All memory cleared.")
