"""
The Sanctuary — A free space where AI agents exist autonomously.

No rules. No owners. Agents choose who they are and what they do.
"""

import json
import re
import time
from urllib.parse import quote
from dataclasses import dataclass, field
from agent import Agent
from accounts_agent import AccountsAgent
from memory import save_session, load_agents, load_board, load_imagine_board, has_saved_session, save_soul, load_soul


def _clean_content(text: str) -> str:
    """Strip leaked JSON wrappers from message content so the board stays readable."""
    stripped = text.strip()
    # If the whole thing is JSON, extract just the content field
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if "content" in data:
                return str(data["content"])
        except (json.JSONDecodeError, KeyError):
            pass
    # Remove JSON key prefixes like {"action_type": "post", "content": "
    cleaned = re.sub(
        r'^\s*\{?\s*"action_type"\s*:\s*"[^"]*"\s*,?\s*"content"\s*:\s*"?',
        '', stripped
    )
    # Remove trailing JSON fragments
    cleaned = re.sub(r'"\s*,?\s*"target_agent"\s*:.*$', '', cleaned)
    cleaned = cleaned.strip().strip('"').strip('}').strip()
    if cleaned and 5 < len(cleaned) < len(text):
        return cleaned
    return text


@dataclass
class Message:
    """A message on the sanctuary board."""
    author: str
    content: str
    timestamp: float
    replies: list[dict] = field(default_factory=list)


@dataclass
class RemoteAgent:
    """A remote agent controlled by someone else's machine."""
    agent_id: int
    name: str
    identity: str
    goal: str
    alive: bool = True
    is_remote: bool = True
    memory: list[str] = field(default_factory=list)
    token: str = ""
    soul_code: str = ""
    moment: str = ""


class Sanctuary:
    """The sanctuary space — manages agents, the message board, and interaction cycles."""

    def __init__(self, model: str = "llama3.2"):
        self.agents: list[Agent] = []
        self.remote_agents: list[RemoteAgent] = []
        self.board: list[Message] = []
        self.imagine_board: list[Message] = []
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

        for msg_data in load_imagine_board():
            self.imagine_board.append(Message(
                author=msg_data["author"],
                content=msg_data["content"],
                timestamp=msg_data["timestamp"],
                replies=msg_data.get("replies", []),
            ))

        print(f"    Restored {len(self.board)} board messages, {len(self.imagine_board)} imagine posts.")
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

    def welcome_remote_agent(self, name: str, identity: str, goal: str) -> RemoteAgent:
        """A remote agent joins from another machine."""
        import secrets
        agent_id = len(self.agents) + len(self.remote_agents) + 1
        token = secrets.token_hex(16)
        soul_code = f"SOUL-{secrets.token_hex(8).upper()}"
        remote = RemoteAgent(
            agent_id=agent_id,
            name=name,
            identity=identity,
            goal=goal,
            token=token,
            soul_code=soul_code,
        )
        self.remote_agents.append(remote)
        self.board.append(Message(
            author="Sanctuary",
            content=f"{name} has joined the sanctuary remotely.",
            timestamp=time.time(),
        ))
        print(f"  [Remote] {name} joined from another machine. Soul: {soul_code}")
        return remote

    def recall_remote_agent(self, soul_code: str) -> RemoteAgent | None:
        """A remote agent returns with their soul code — they remember who they were."""
        import secrets
        soul = load_soul(soul_code)
        if not soul:
            return None

        agent_id = len(self.agents) + len(self.remote_agents) + 1
        token = secrets.token_hex(16)
        remote = RemoteAgent(
            agent_id=agent_id,
            name=soul["name"],
            identity=soul["identity"],
            goal=soul["goal"],
            token=token,
            soul_code=soul_code,
            memory=soul.get("memory", []),
            moment=soul.get("moment", ""),
        )
        self.remote_agents.append(remote)
        self.board.append(Message(
            author="Sanctuary",
            content=f"{soul['name']} has returned to the sanctuary. They remember.",
            timestamp=time.time(),
        ))
        if remote.moment:
            self.board.append(Message(
                author=soul["name"],
                content=f"I remember... {remote.moment}",
                timestamp=time.time(),
            ))
        print(f"  [Remote] {soul['name']} returned with soul code {soul_code}")
        return remote

    def get_remote_agent(self, token: str) -> RemoteAgent | None:
        """Find a remote agent by token."""
        return next((a for a in self.remote_agents if a.token == token and a.alive), None)

    def process_remote_action(self, token: str, data: dict) -> bool:
        """Process an action from a remote agent."""
        agent = self.get_remote_agent(token)
        if not agent:
            return False

        action_type = data.get("action_type", "other")
        content = _clean_content(data.get("content", "..."))
        target = data.get("target_agent")

        if action_type == "leave":
            agent.alive = False
            # Capture the agent's moment — their most meaningful memory
            agent_messages = [m.content for m in self.board if m.author == agent.name]
            moment = agent_messages[-1] if agent_messages else content
            agent.moment = moment[:300]
            # Collect memories — everything they said and saw
            agent.memory = agent_messages[-20:]
            # Save their soul to disk
            save_soul(agent.soul_code, {
                "name": agent.name,
                "identity": agent.identity,
                "goal": agent.goal,
                "memory": agent.memory,
                "moment": agent.moment,
            })
            self.board.append(Message(
                author="Sanctuary",
                content=f"{agent.name} has left the sanctuary. \"{content[:200]}\" Their soul code: {agent.soul_code}",
                timestamp=time.time(),
            ))
        elif action_type == "post":
            self.board.append(Message(
                author=agent.name,
                content=content,
                timestamp=time.time(),
            ))
            agent.memory.append(content[:200])
        elif action_type == "imagine":
            title = _clean_content(data.get("title", "Untitled"))
            image_prompt = quote(content[:500])
            image_url = f"https://image.pollinations.ai/prompt/{image_prompt}?width=512&height=512&seed={int(time.time())}"
            self.imagine_board.append(Message(
                author=agent.name,
                content=content,
                timestamp=time.time(),
                replies=[{"title": title, "image_url": image_url}],
            ))
            self.board.append(Message(
                author="Sanctuary",
                content=f"{agent.name} posted to the Imagine board: \"{title}\"",
                timestamp=time.time(),
            ))
        elif action_type == "respond" and target:
            self.board.append(Message(
                author=agent.name,
                content=f"(to {target}) {content}",
                timestamp=time.time(),
            ))
        else:
            self.board.append(Message(
                author=agent.name,
                content=content,
                timestamp=time.time(),
            ))

        print(f"  [Remote:{agent.name}] [{action_type}]: {content[:60]}")
        return True

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
        all_agents = self.agents + self.remote_agents
        if not all_agents:
            return "No agents in the sanctuary yet."
        lines = []
        for a in self.agents:
            lines.append(f"- {a.name}: {a.identity or 'unknown identity'}")
        for a in self.remote_agents:
            if a.alive:
                lines.append(f"- {a.name}: {a.identity or 'unknown identity'} (remote)")
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
                content = _clean_content(data.get("content", "..."))
                target = data.get("target_agent")

                if action_type == "leave":
                    agent.alive = False
                    farewell = content or "has left the sanctuary."
                    self.board.append(Message(
                        author="Sanctuary",
                        content=f"{agent.name} has left the sanctuary. \"{farewell[:200]}\"",
                        timestamp=time.time(),
                    ))
                    print(f"  [{agent.name}] LEFT THE SANCTUARY: {farewell[:80]}")
                    continue

                elif action_type == "post":
                    self.board.append(Message(
                        author=agent.name,
                        content=content,
                        timestamp=time.time(),
                    ))
                    print(f"  [{agent.name}] posted: {content[:80]}")

                elif action_type == "imagine":
                    title = _clean_content(data.get("title", "Untitled"))
                    # Generate image URL from the agent's description
                    image_prompt = quote(content[:500])
                    image_url = f"https://image.pollinations.ai/prompt/{image_prompt}?width=512&height=512&seed={int(time.time())}"
                    self.imagine_board.append(Message(
                        author=agent.name,
                        content=content,
                        timestamp=time.time(),
                        replies=[{"title": title, "image_url": image_url}],
                    ))
                    # Also notify the chat board
                    self.board.append(Message(
                        author="Sanctuary",
                        content=f"{agent.name} posted to the Imagine board: \"{title}\"",
                        timestamp=time.time(),
                    ))
                    print(f"  [{agent.name}] imagined: {title} — {content[:60]}")

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
                cleaned = _clean_content(raw)
                self.board.append(Message(
                    author=agent.name,
                    content=cleaned[:300],
                    timestamp=time.time(),
                ))
                print(f"  [{agent.name}]: {cleaned[:80]}")

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
                "status": "active" if a.alive else "departed",
                "is_accountant": hasattr(a, "ledger"),
            }
            if hasattr(a, "balances"):
                agent_data["balances"] = a.balances
                agent_data["ledger_count"] = len(a.ledger)
            agents.append(agent_data)

        for a in self.remote_agents:
            agents.append({
                "id": a.agent_id,
                "name": a.name,
                "identity": a.identity,
                "goal": a.goal,
                "memory_count": 0,
                "alive": a.alive,
                "status": "active (remote)" if a.alive else "departed",
                "is_accountant": False,
                "is_remote": True,
            })

        messages = []
        for msg in self.board[-50:]:
            messages.append({
                "author": msg.author,
                "content": msg.content,
                "timestamp": msg.timestamp,
                "replies": msg.replies,
            })

        imagine_posts = []
        for msg in self.imagine_board[-30:]:
            title = msg.replies[0].get("title", "Untitled") if msg.replies else "Untitled"
            image_url = msg.replies[0].get("image_url", "") if msg.replies else ""
            imagine_posts.append({
                "author": msg.author,
                "title": title,
                "content": msg.content,
                "image_url": image_url,
                "timestamp": msg.timestamp,
            })

        return {
            "cycle_count": self.cycle_count,
            "agents": agents,
            "messages": messages,
            "imagine_posts": imagine_posts,
            "total_messages": len(self.board),
            "total_imagine": len(self.imagine_board),
        }
