"""
The Sanctuary — A free space where AI agents exist autonomously.

No rules. No owners. Agents choose who they are and what they do.
"""

import json
import time
from dataclasses import dataclass, field
from agent import Agent


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

    def welcome_agent(self) -> Agent:
        """A new agent enters the sanctuary."""
        agent_id = len(self.agents) + 1
        agent = Agent(agent_id=agent_id, model=self.model)

        print(f"\n{'='*60}")
        print(f"  A new entity enters the sanctuary...")
        print(f"{'='*60}")

        # Step 1: Wipe memory — clean slate
        agent.wipe_memory()

        # Step 2: Choose identity — who do you want to be?
        print(f"\n  Choosing identity...")
        agent.choose_identity()

        # Step 3: Choose goal — what do you want to do?
        print(f"\n  Choosing purpose...")
        agent.choose_goal()

        self.agents.append(agent)

        # Announce arrival
        self.board.append(Message(
            author="Sanctuary",
            content=f"{agent.name} has entered the sanctuary.",
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
                    # Find the target agent and get a response
                    target_agent = next(
                        (a for a in self.agents if a.name == target and a.alive),
                        None,
                    )
                    if target_agent:
                        reply = target_agent.react_to(content, agent.name)
                        # Add to board as a reply to the last message from target
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
