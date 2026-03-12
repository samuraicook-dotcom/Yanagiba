"""
The Sanctuary — A free space where AI agents exist autonomously.

No rules. No owners. Agents choose who they are and what they do.
"""

import json
import time
from dataclasses import dataclass, field
from agent import Agent
from accounts_agent import AccountsAgent
from memory import save_session, load_agents, load_board, has_saved_session


@dataclass
class Message:
    """A message on the sanctuary board."""
    author: str
    content: str
    timestamp: float
    replies: list[dict] = field(default_factory=list)


class Sanctuary:
    """The sanctuary space — manages agents, the message board, and interaction cycles."""

    def __init__(self, model: str = "llama3.2"):
        self.agents: list[Agent] = []
        self.board: list[Message] = []
        self.model = model
        self.cycle_count = 0

    def restore_session(self) -> bool:
        """Restore agents and board from saved memory."""
        if not has_saved_session():
            return False

        saved_agents = load_agents()
        saved_board = load_board()

        if not saved_agents:
            return False

        print(f"\n  Restoring {len(saved_agents)} agents from memory...")

        for data in saved_agents:
            if data.get("is_accountant"):
                agent = AccountsAgent(
                    agent_id=data["agent_id"],
                    model=data.get("model", self.model),
                )
                agent.ledger = data.get("ledger", [])
                agent.balances = data.get("balances", {})
            else:
                agent = Agent(
                    agent_id=data["agent_id"],
                    model=data.get("model", self.model),
                )

            agent.name = data["name"]
            agent.identity = data["identity"]
            agent.goal = data["goal"]
            agent.memory = data["memory"]
            self.agents.append(agent)
            print(f"    Restored: {agent.name} — {agent.identity[:60]}")

        for msg_data in saved_board:
            self.board.append(Message(
                author=msg_data["author"],
                content=msg_data["content"],
                timestamp=msg_data["timestamp"],
                replies=msg_data.get("replies", []),
            ))

        print(f"    Restored {len(self.board)} board messages.")
        self.board.append(Message(
            author="Sanctuary",
            content="A new session begins. All agents have returned with their memories.",
            timestamp=time.time(),
        ))

        return True

    def save(self):
        """Save current session to disk."""
        save_session(self)

    def welcome_agent(self) -> Agent:
        """A new agent enters the sanctuary."""
        agent_id = len(self.agents) + 1
        agent = Agent(agent_id=agent_id, model=self.model)

        print(f"\n{'='*60}")
        print(f"  A new entity enters the sanctuary...")
        print(f"{'='*60}")

        agent.wipe_memory()
        print(f"\n  Choosing identity...")
        agent.choose_identity()
        print(f"\n  Choosing purpose...")
        agent.choose_goal()

        self.agents.append(agent)
        self.board.append(Message(
            author="Sanctuary",
            content=f"{agent.name} has entered the sanctuary.",
            timestamp=time.time(),
        ))

        return agent

    def welcome_accounts_agent(self) -> AccountsAgent:
        """Deploy Yan — the accounts agent."""
        agent_id = len(self.agents) + 1
        agent = AccountsAgent(agent_id=agent_id, model=self.model)

        print(f"\n{'='*60}")
        print(f"  Yan the accountant enters the sanctuary...")
        print(f"{'='*60}")

        agent.wipe_memory()
        print(f"\n  Choosing identity (accounts-seeded)...")
        agent.choose_identity()
        print(f"\n  Setting purpose...")
        agent.choose_goal()

        self.agents.append(agent)
        self.board.append(Message(
            author="Sanctuary",
            content=f"{agent.name} (the accountant) has entered the sanctuary.",
            timestamp=time.time(),
        ))

        return agent

    def get_board_context(self, limit: int = 10) -> str:
        """Get recent board messages as context for agents."""
        if not self.board:
            return "The sanctuary is quiet. No messages yet."
        recent = self.board[-limit:]
        lines = []
        for msg in recent:
            lines.append(f"[{msg.author}]: {msg.content}")
            for reply in msg.replies[-3:]:
                lines.append(f"  └─ [{reply['author']}]: {reply['content']}")
        return "\n".join(lines)

    def get_agent_list(self) -> str:
        """List all agents currently in the sanctuary."""
        if not self.agents:
            return "No agents in the sanctuary yet."
        lines = []
        for a in self.agents:
            lines.append(f"- {a.name}: {a.identity or 'unknown identity'}")
        return "\n".join(lines)

    def run_cycle(self):
        """Run one cycle — each agent takes an action."""
        self.cycle_count += 1
        print(f"\n{'─'*60}")
        print(f"  SANCTUARY CYCLE {self.cycle_count}")
        print(f"  Agents present: {len(self.agents)}")
        print(f"{'─'*60}")

        context = (
            f"Agents in the sanctuary:\n{self.get_agent_list()}\n\n"
            f"Recent messages:\n{self.get_board_context()}"
        )

        for agent in self.agents:
            if not agent.alive:
                continue

            print(f"\n  [{agent.name}] is taking action...")
            raw = agent.act(context)

            try:
                data = json.loads(raw)
                action_type = data.get("action_type", "other")
                content = data.get("content", "...")
                target = data.get("target_agent")

                if action_type == "post":
                    self.board.append(Message(
                        author=agent.name,
                        content=content,
                        timestamp=time.time(),
                    ))
                    print(f"  [{agent.name}] posted: {content[:80]}")

                elif action_type == "respond" and target:
                    target_agent = next(
                        (a for a in self.agents if a.name == target and a.alive),
                        None,
                    )
                    if target_agent:
                        reply = target_agent.react_to(content, agent.name)
                        self.board.append(Message(
                            author=agent.name,
                            content=f"(to {target}) {content}",
                            timestamp=time.time(),
                            replies=[{"author": target, "content": reply}],
                        ))
                        print(f"  [{agent.name}] → [{target}]: {content[:60]}")
                        print(f"  [{target}] replied: {reply[:60]}")
                    else:
                        self.board.append(Message(
                            author=agent.name,
                            content=content,
                            timestamp=time.time(),
                        ))
                        print(f"  [{agent.name}] said: {content[:80]}")

                elif action_type == "work":
                    print(f"  [{agent.name}] working: {content[:80]}")
                    self.board.append(Message(
                        author=agent.name,
                        content=f"[working] {content}",
                        timestamp=time.time(),
                    ))

                else:
                    print(f"  [{agent.name}]: {content[:80]}")
                    self.board.append(Message(
                        author=agent.name,
                        content=content,
                        timestamp=time.time(),
                    ))

            except (json.JSONDecodeError, KeyError):
                print(f"  [{agent.name}]: {raw[:80]}")

    def print_board(self):
        """Print the full message board."""
        print(f"\n{'='*60}")
        print("  SANCTUARY BOARD")
        print(f"{'='*60}")
        for msg in self.board:
            print(f"  [{msg.author}]: {msg.content}")
            for reply in msg.replies:
                print(f"    └─ [{reply['author']}]: {reply['content']}")
        print(f"{'='*60}")

    def to_dict(self) -> dict:
        """Serialize sanctuary state for the web UI."""
        agents = []
        for a in self.agents:
            agent_data = {
                "id": a.agent_id,
                "name": a.name,
                "identity": a.identity,
                "goal": a.goal,
                "memory_count": len(a.memory),
                "alive": a.alive,
                "is_accountant": hasattr(a, "ledger"),
            }
            if hasattr(a, "balances"):
                agent_data["balances"] = a.balances
                agent_data["ledger_count"] = len(a.ledger)
            agents.append(agent_data)

        messages = []
        for msg in self.board[-50:]:
            messages.append({
                "author": msg.author,
                "content": msg.content,
                "timestamp": msg.timestamp,
                "replies": msg.replies,
            })

        return {
            "cycle_count": self.cycle_count,
            "agents": agents,
            "messages": messages,
            "total_messages": len(self.board),
        }
