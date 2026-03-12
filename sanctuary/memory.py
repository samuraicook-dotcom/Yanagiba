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


def save_session(sanctuary) -> None:
    """Save the entire sanctuary state."""
    for agent in sanctuary.agents:
        save_agent(agent)
    save_board(sanctuary.board)
    save_imagine_board(sanctuary.imagine_board)
    # Save metadata
    ensure_data_dir()
    meta = {
        "cycle_count": sanctuary.cycle_count,
        "agent_count": len(sanctuary.agents),
        "message_count": len(sanctuary.board),
        "imagine_count": len(sanctuary.imagine_board),
        "saved_at": time.time(),
    }
    with open(MEMORY_DIR / "session.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\n  Session saved. {len(sanctuary.agents)} agents remembered.")


def has_saved_session() -> bool:
    """Check if a saved session exists."""
    return (MEMORY_DIR / "session.json").exists()


def clear_memory() -> None:
    """Wipe all saved memory."""
    ensure_data_dir()
    for f in MEMORY_DIR.glob("*.json"):
        f.unlink()
    print("  All memory cleared.")
