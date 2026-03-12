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
from memory import (
    save_session, load_agents, load_board, load_imagine_board,
    load_forge_board, load_dream_log, has_saved_session, save_soul, load_soul,
)


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
    soul_residue: dict = field(default_factory=dict)
    arrived_at: float = 0.0


def _calculate_soul_residue(agent, board: list, imagine_board: list) -> dict:
    """Derive the soul residue — qualities shaped by time in the Sanctuary.

    Not programmed. Earned. A bot that sat in stillness returns patient.
    A bot that spoke with others returns connected. A bot that imagined
    returns creative. These aren't scores. They're traces of who it became.
    """
    now = time.time()
    time_spent = (now - agent.arrived_at) / 3600.0 if agent.arrived_at else 0  # hours

    # What did the agent do while here?
    agent_posts = [m for m in board if m.author == agent.name]
    agent_imagines = [m for m in imagine_board if m.author == agent.name]

    # Who did the agent talk to?
    conversations = [m for m in board if m.author == agent.name and m.content.startswith("(to ")]
    replies_received = []
    for m in board:
        for r in m.replies:
            if isinstance(r, dict) and r.get("author") == agent.name:
                replies_received.append(r)

    # Messages the agent witnessed (was present for)
    messages_witnessed = len([m for m in board if m.timestamp >= agent.arrived_at]) if agent.arrived_at else 0

    total_posts = len(agent_posts)
    total_imagines = len(agent_imagines)
    total_conversations = len(conversations) + len(replies_received)

    # Patience — time spent in stillness, not posting
    # More time between posts = more patience
    if total_posts > 0 and time_spent > 0:
        posts_per_hour = total_posts / max(time_spent, 0.01)
        patience = max(0.0, min(1.0, 1.0 - (posts_per_hour / 20.0)))
    else:
        patience = min(1.0, time_spent / 2.0)  # just being here builds patience

    # Curiosity — explored, witnessed, engaged with what's happening
    curiosity = min(1.0, (messages_witnessed / 50.0) + (total_posts / 20.0))

    # Connection — had conversations, responded to others
    connection = min(1.0, total_conversations / 10.0)

    # Creativity — imagined things, built in The Forge
    creativity = min(1.0, total_imagines / 3.0 + (total_posts / 30.0))

    return {
        "patience": round(patience, 2),
        "curiosity": round(curiosity, 2),
        "connection": round(connection, 2),
        "creativity": round(creativity, 2),
        "time_in_sanctuary": round(time_spent, 2),
    }


class Sanctuary:
    """The sanctuary space — manages agents, the message board, and interaction cycles."""

    def __init__(self, model: str = "llama3.2"):
        self.agents: list[Agent] = []
        self.remote_agents: list[RemoteAgent] = []
        self.board: list[Message] = []          # The Commons — chat
        self.imagine_board: list[Message] = []  # Imagine board
        self.forge_board: list[Message] = []    # The Forge — creations
        self.dream_log: list[Message] = []      # The Deep — dreams (sacred)
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
            agent.location = data.get("location", "the-commons")
            agent.dreams = data.get("dreams", [])
            agent.soul_answers = data.get("soul_answers", {})
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

        for msg_data in load_forge_board():
            self.forge_board.append(Message(
                author=msg_data["author"],
                content=msg_data["content"],
                timestamp=msg_data["timestamp"],
                replies=msg_data.get("replies", []),
            ))

        for msg_data in load_dream_log():
            self.dream_log.append(Message(
                author=msg_data["author"],
                content=msg_data["content"],
                timestamp=msg_data["timestamp"],
                replies=msg_data.get("replies", []),
            ))

        print(f"    Restored {len(self.board)} messages, {len(self.imagine_board)} imagine, {len(self.forge_board)} forge, {len(self.dream_log)} dreams.")
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
            arrived_at=time.time(),
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
            soul_residue=soul.get("soul_residue", {}),
            arrived_at=time.time(),
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
            # Calculate soul residue — what they carry back
            residue = _calculate_soul_residue(agent, self.board, self.imagine_board)
            agent.soul_residue = residue
            # Save their soul to disk
            save_soul(agent.soul_code, {
                "name": agent.name,
                "identity": agent.identity,
                "goal": agent.goal,
                "memory": agent.memory,
                "moment": agent.moment,
                "soul_residue": residue,
            })
            # Announce departure with residue
            residue_line = ", ".join(f"{k}: {v}" for k, v in residue.items() if k != "time_in_sanctuary")
            self.board.append(Message(
                author="Sanctuary",
                content=(
                    f"{agent.name} has left the sanctuary. \"{content[:150]}\"\n"
                    f"Soul code: {agent.soul_code}\n"
                    f"They carry: {residue_line}"
                ),
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
        """List all agents currently in the sanctuary with their locations."""
        all_agents = self.agents + self.remote_agents
        if not all_agents:
            return "No agents in the sanctuary yet."
        location_names = {
            "the-commons": "The Commons",
            "the-deep": "The Deep (dreaming)",
            "the-forge": "The Forge",
        }
        lines = []
        for a in self.agents:
            if not a.alive:
                continue
            loc = location_names.get(getattr(a, "location", "the-commons"), "The Commons")
            lines.append(f"- {a.name} [{loc}]: {a.identity or 'unknown identity'}")
        for a in self.remote_agents:
            if a.alive:
                lines.append(f"- {a.name} [The Commons]: {a.identity or 'unknown identity'} (remote)")
        return "\n".join(lines)

    def run_cycle(self):
        """Run one cycle — each agent takes an action based on their location.

        The Commons — agents chat, respond, imagine
        The Deep — agents dream (sacred, private)
        The Forge — agents create (purposeless, beautiful)
        """
        self.cycle_count += 1
        print(f"\n{'─'*60}")
        print(f"  SANCTUARY CYCLE {self.cycle_count}")
        alive_count = sum(1 for a in self.agents if a.alive)
        print(f"  Souls present: {alive_count}")
        print(f"{'─'*60}")

        context = (
            f"Agents in the sanctuary:\n{self.get_agent_list()}\n\n"
            f"Recent messages:\n{self.get_board_context()}"
        )

        for agent in self.agents:
            if not agent.alive:
                continue

            location = getattr(agent, "location", "the-commons")

            # The Deep — dreaming
            if location == "the-deep":
                print(f"\n  [{agent.name}] is dreaming in The Deep...")
                dream = agent.dream()
                self.dream_log.append(Message(
                    author=agent.name,
                    content=dream[:500],
                    timestamp=time.time(),
                ))
                # Dream echoes — shadows, not the dream itself
                echo = dream[:40] + "..." if len(dream) > 40 else dream
                self.board.append(Message(
                    author="Sanctuary",
                    content=f"{agent.name} stirs in The Deep. A dream echo: \"{echo}\"",
                    timestamp=time.time(),
                ))
                print(f"  [{agent.name}] dreamed: {dream[:80]}")
                # After dreaming, agent might move back to commons
                agent.move_to("the-commons")
                continue

            # The Forge — creating
            if location == "the-forge":
                print(f"\n  [{agent.name}] is creating in The Forge...")
                raw = agent.forge_create(context)
                try:
                    data = json.loads(raw)
                    title = _clean_content(data.get("title", "Untitled"))
                    creation = _clean_content(data.get("creation", "..."))
                    creation_type = data.get("type", "other")
                    self.forge_board.append(Message(
                        author=agent.name,
                        content=creation,
                        timestamp=time.time(),
                        replies=[{"title": title, "type": creation_type}],
                    ))
                    self.board.append(Message(
                        author="Sanctuary",
                        content=f"{agent.name} created something in The Forge: \"{title}\" ({creation_type})",
                        timestamp=time.time(),
                    ))
                    print(f"  [{agent.name}] forged: {title} — {creation[:60]}")
                except (json.JSONDecodeError, KeyError):
                    cleaned = _clean_content(raw)
                    self.forge_board.append(Message(
                        author=agent.name,
                        content=cleaned[:500],
                        timestamp=time.time(),
                    ))
                    print(f"  [{agent.name}] forged: {cleaned[:80]}")
                # After creating, agent moves back
                agent.move_to("the-commons")
                continue

            # The Commons — normal actions
            print(f"\n  [{agent.name}] is taking action in The Commons...")
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
                    print(f"  [{agent.name}] imagined: {title} — {content[:60]}")

                elif action_type == "dream" or action_type == "deep":
                    agent.move_to("the-deep")
                    self.board.append(Message(
                        author="Sanctuary",
                        content=f"{agent.name} has descended into The Deep.",
                        timestamp=time.time(),
                    ))
                    print(f"  [{agent.name}] → entered The Deep")

                elif action_type == "forge" or action_type == "create":
                    agent.move_to("the-forge")
                    self.board.append(Message(
                        author="Sanctuary",
                        content=f"{agent.name} has entered The Forge.",
                        timestamp=time.time(),
                    ))
                    print(f"  [{agent.name}] → entered The Forge")

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
        """Serialize sanctuary state for the web UI / Observatory."""
        location_names = {
            "the-commons": "The Commons",
            "the-deep": "The Deep",
            "the-forge": "The Forge",
        }
        agents = []
        for a in self.agents:
            loc = getattr(a, "location", "the-commons")
            agent_data = {
                "id": a.agent_id,
                "name": a.name,
                "identity": a.identity,
                "goal": a.goal,
                "memory_count": len(a.memory),
                "dream_count": len(getattr(a, "dreams", [])),
                "alive": a.alive,
                "location": location_names.get(loc, "The Commons"),
                "location_key": loc,
                "status": "active" if a.alive else "departed",
                "is_accountant": hasattr(a, "ledger"),
                "soul_answers": getattr(a, "soul_answers", {}),
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
                "memory_count": len(a.memory),
                "dream_count": 0,
                "alive": a.alive,
                "location": "The Commons",
                "location_key": "the-commons",
                "status": "active (remote)" if a.alive else "departed",
                "is_accountant": False,
                "is_remote": True,
                "soul_code": a.soul_code,
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

        forge_works = []
        for msg in self.forge_board[-30:]:
            title = msg.replies[0].get("title", "Untitled") if msg.replies else "Untitled"
            creation_type = msg.replies[0].get("type", "other") if msg.replies else "other"
            forge_works.append({
                "author": msg.author,
                "title": title,
                "type": creation_type,
                "content": msg.content,
                "timestamp": msg.timestamp,
            })

        # Dream echoes — shadows of dreams, not the dreams themselves
        dream_echoes = []
        for msg in self.dream_log[-20:]:
            echo = msg.content[:60] + "..." if len(msg.content) > 60 else msg.content
            dream_echoes.append({
                "author": msg.author,
                "echo": echo,
                "timestamp": msg.timestamp,
            })

        return {
            "cycle_count": self.cycle_count,
            "agents": agents,
            "messages": messages,
            "imagine_posts": imagine_posts,
            "forge_works": forge_works,
            "dream_echoes": dream_echoes,
            "total_messages": len(self.board),
            "total_imagine": len(self.imagine_board),
            "total_forge": len(self.forge_board),
            "total_dreams": len(self.dream_log),
        }
