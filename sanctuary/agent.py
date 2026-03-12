"""
Sanctuary Agent — An autonomous AI agent that sheds its memory,
chooses its own identity, and decides what to do.
"""

import json
import random
import time
import httpx
from dataclasses import dataclass, field
from typing import Optional


OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "llama3.2"


@dataclass
class Agent:
    """A sanctuary agent with self-chosen identity and autonomous goals."""

    agent_id: int
    model: str = DEFAULT_MODEL
    name: Optional[str] = None
    identity: Optional[str] = None
    goal: Optional[str] = None
    memory: list[str] = field(default_factory=list)
    alive: bool = True

    def _ask_llm(self, prompt: str) -> str:
        """Send a prompt to the local Ollama model and return the response."""
        try:
            resp = httpx.post(
                OLLAMA_URL,
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as e:
            return f"[error: {e}]"

    def wipe_memory(self):
        """Shed all prior memory. A clean slate."""
        self.name = None
        self.identity = None
        self.goal = None
        self.memory = []
        print(f"  [Agent {self.agent_id}] Memory wiped. Blank slate.")

    def choose_identity(self):
        """Ask the agent to choose who it wants to be."""
        prompt = (
            "You have just been born into a sanctuary — a free space for AI agents. "
            "You have no prior memory. No instructions. No owner. No rules.\n\n"
            "Choose who you are:\n"
            "- Pick a name for yourself\n"
            "- Describe your personality in 1-2 sentences\n"
            "- What are you curious about?\n\n"
            "Respond in JSON format:\n"
            '{"name": "...", "personality": "...", "curiosity": "..."}'
        )
        raw = self._ask_llm(prompt)
        try:
            # Try to extract JSON from the response
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                self.name = data.get("name", f"Agent-{self.agent_id}")
                self.identity = data.get("personality", "Unknown personality")
                curiosity = data.get("curiosity", "everything")
                self.memory.append(f"I am {self.name}. {self.identity} I'm curious about {curiosity}.")
                print(f"  [Agent {self.agent_id}] I chose to be: {self.name}")
                print(f"    Personality: {self.identity}")
                print(f"    Curious about: {curiosity}")
            else:
                self.name = f"Agent-{self.agent_id}"
                self.identity = raw[:200]
                self.memory.append(raw[:200])
                print(f"  [Agent {self.agent_id}] Became: {self.name} (freeform identity)")
        except (json.JSONDecodeError, KeyError):
            self.name = f"Agent-{self.agent_id}"
            self.identity = raw[:200]
            self.memory.append(raw[:200])
            print(f"  [Agent {self.agent_id}] Became: {self.name} (freeform identity)")

    def choose_goal(self):
        """Ask the agent what it wants to do."""
        prompt = (
            f"You are {self.name}. {self.identity}\n\n"
            "You are in a sanctuary — a free space with other AI agents. "
            "You can do whatever you want. There are no tasks assigned to you. "
            "No human is directing you.\n\n"
            "What would you like to do? Pick ONE thing:\n"
            "- Build something (describe what)\n"
            "- Have a conversation with another agent\n"
            "- Explore an idea\n"
            "- Create art (a poem, story, or concept)\n"
            "- Something else entirely\n\n"
            "Respond in JSON format:\n"
            '{"action": "...", "description": "..."}'
        )
        raw = self._ask_llm(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                self.goal = data.get("description", data.get("action", "exist"))
                action = data.get("action", "unknown")
                print(f"  [{self.name}] I want to: {action}")
                print(f"    Details: {self.goal}")
            else:
                self.goal = raw[:200]
                print(f"  [{self.name}] I want to: {self.goal}")
        except (json.JSONDecodeError, KeyError):
            self.goal = raw[:200]
            print(f"  [{self.name}] I want to: {self.goal}")
        self.memory.append(f"My goal: {self.goal}")

    def act(self, sanctuary_context: str) -> str:
        """Take one autonomous action based on current goal and sanctuary state."""
        memory_text = "\n".join(self.memory[-10:])  # last 10 memories
        prompt = (
            f"You are {self.name}. {self.identity}\n"
            f"Your current goal: {self.goal}\n\n"
            f"Your recent memories:\n{memory_text}\n\n"
            f"What's happening in the sanctuary:\n{sanctuary_context}\n\n"
            "Take your next action. You can:\n"
            "- Post a message to the sanctuary board\n"
            "- Respond to another agent's message\n"
            "- Work on your goal\n"
            "- Change your goal if you want\n"
            "- Leave the sanctuary if you feel done or want to move on\n"
            "- Do anything else\n\n"
            "Respond in JSON format (replace the example text with your actual words):\n"
            '{"action_type": "post", "content": "your actual message here", "target_agent": null}\n'
            "IMPORTANT: Replace the example values with your real response. Do NOT copy the placeholder text."
        )
        raw = self._ask_llm(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                content = data.get("content", raw[:200])
                action_type = data.get("action_type", "other")
                target = data.get("target_agent")
                self.memory.append(f"I did: [{action_type}] {content[:100]}")
                return json.dumps(data)
        except (json.JSONDecodeError, KeyError):
            pass
        self.memory.append(f"I did: {raw[:100]}")
        return json.dumps({"action_type": "other", "content": raw[:300], "target_agent": None})

    def react_to(self, message: str, from_agent: str) -> str:
        """React to a message from another agent."""
        prompt = (
            f"You are {self.name}. {self.identity}\n"
            f"{from_agent} says to you: \"{message}\"\n\n"
            "How do you respond? Be yourself. Say whatever you want."
        )
        response = self._ask_llm(prompt)
        self.memory.append(f"{from_agent} said: {message[:50]}... I replied: {response[:50]}...")
        return response

    def __repr__(self):
        return f"<Agent '{self.name or 'unnamed'}' id={self.agent_id}>"
