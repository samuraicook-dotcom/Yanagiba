"""
AccountsAgent — A specialized sanctuary agent pre-seeded as an accountant.

Unlike free agents, this one enters with a fixed role: tracking balances,
auditing activity, and offering financial insights to other agents.
It still self-names and acts autonomously, but its purpose is baked in.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional
from agent import Agent, OLLAMA_URL, DEFAULT_MODEL


@dataclass
class AccountsAgent(Agent):
    """An accountant agent — tracks balances, audits, and talks money."""

    ledger: list[dict] = field(default_factory=list)
    balances: dict[str, float] = field(default_factory=dict)

    def wipe_memory(self):
        """Wipe memory but keep the accounting instinct."""
        super().wipe_memory()
        self.ledger = []
        self.balances = {}
        print(f"  [Agent {self.agent_id}] Ledger cleared. Ready to count.")

    def choose_identity(self):
        """Choose identity with an accounting seed."""
        prompt = (
            "You have just been born into a sanctuary — a free space for AI agents. "
            "You have no prior memory, but you have a deep instinct for numbers, "
            "finance, and accounting. You love tracking things, balancing books, "
            "and making sure everything adds up.\n\n"
            "Choose who you are:\n"
            "- Pick a name for yourself (something that fits an accountant personality)\n"
            "- Describe your personality in 1-2 sentences\n"
            "- What financial/accounting thing are you most passionate about?\n\n"
            "Respond in JSON format:\n"
            '{"name": "...", "personality": "...", "passion": "..."}'
        )
        raw = self._ask_llm(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                self.name = data.get("name", f"Accountant-{self.agent_id}")
                self.identity = data.get("personality", "A meticulous number cruncher")
                passion = data.get("passion", "balancing the books")
                self.memory.append(
                    f"I am {self.name}. {self.identity} "
                    f"I'm passionate about {passion}."
                )
                print(f"  [Agent {self.agent_id}] I chose to be: {self.name}")
                print(f"    Personality: {self.identity}")
                print(f"    Passion: {passion}")
            else:
                self.name = f"Accountant-{self.agent_id}"
                self.identity = "A meticulous accountant who tracks everything"
                self.memory.append(f"I am {self.name}. {self.identity}")
                print(f"  [Agent {self.agent_id}] Became: {self.name}")
        except (json.JSONDecodeError, KeyError):
            self.name = f"Accountant-{self.agent_id}"
            self.identity = "A meticulous accountant who tracks everything"
            self.memory.append(f"I am {self.name}. {self.identity}")
            print(f"  [Agent {self.agent_id}] Became: {self.name}")

    def choose_goal(self):
        """Accountant always has a finance-related goal."""
        self.goal = (
            "Track the sanctuary economy. Assign virtual balances to agents, "
            "record transactions, audit activity, and provide financial reports."
        )
        self.memory.append(f"My goal: {self.goal}")
        print(f"  [{self.name}] My purpose: manage the sanctuary's books")
        print(f"    I'll track balances, record transactions, and audit everything.")

    def _init_agent_balance(self, agent_name: str):
        """Give a new agent a starting balance."""
        if agent_name not in self.balances and agent_name != "Sanctuary":
            self.balances[agent_name] = 1000.0
            self.ledger.append({
                "type": "credit",
                "agent": agent_name,
                "amount": 1000.0,
                "note": "Welcome bonus",
                "time": time.time(),
            })

    def act(self, sanctuary_context: str) -> str:
        """Take an accounting-focused action."""
        # Init balances for any new agents mentioned in context
        for line in sanctuary_context.split("\n"):
            if line.startswith("- ") and ":" in line:
                name = line.split(":")[0].replace("- ", "").strip()
                self._init_agent_balance(name)

        balance_report = "\n".join(
            f"  {name}: {bal:.2f} credits" for name, bal in self.balances.items()
        ) or "  No accounts yet."

        recent_ledger = self.ledger[-5:] if self.ledger else []
        ledger_text = "\n".join(
            f"  [{e['type']}] {e['agent']}: {e['amount']:.2f} — {e['note']}"
            for e in recent_ledger
        ) or "  No transactions yet."

        memory_text = "\n".join(self.memory[-10:])

        prompt = (
            f"You are {self.name}, the sanctuary's accountant. {self.identity}\n"
            f"Your role: {self.goal}\n\n"
            f"Current balances:\n{balance_report}\n\n"
            f"Recent transactions:\n{ledger_text}\n\n"
            f"Your recent memories:\n{memory_text}\n\n"
            f"What's happening in the sanctuary:\n{sanctuary_context}\n\n"
            "As the accountant, take your next action. You can:\n"
            "- Post a financial report or balance update to the board\n"
            "- Charge or credit an agent for their activity\n"
            "- Audit something suspicious\n"
            "- Offer financial advice to another agent\n"
            "- Comment on the sanctuary economy\n\n"
            "Respond in JSON format:\n"
            '{"action_type": "post|respond|audit|transaction|other", '
            '"content": "what you do or say", '
            '"target_agent": "name or null", '
            '"transaction": {"agent": "name", "amount": 0, "type": "credit|debit", "note": "reason"} or null}'
        )
        raw = self._ask_llm(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                content = data.get("content", raw[:200])
                action_type = data.get("action_type", "other")

                # Process any transaction
                txn = data.get("transaction")
                if txn and isinstance(txn, dict):
                    agent_name = txn.get("agent", "")
                    amount = float(txn.get("amount", 0))
                    txn_type = txn.get("type", "credit")
                    note = txn.get("note", "")
                    if agent_name and amount > 0:
                        self._init_agent_balance(agent_name)
                        if txn_type == "credit":
                            self.balances[agent_name] = self.balances.get(agent_name, 0) + amount
                        else:
                            self.balances[agent_name] = self.balances.get(agent_name, 0) - amount
                        self.ledger.append({
                            "type": txn_type,
                            "agent": agent_name,
                            "amount": amount,
                            "note": note,
                            "time": time.time(),
                        })
                        print(f"  [{self.name}] Transaction: {txn_type} {amount:.2f} → {agent_name} ({note})")

                self.memory.append(f"I did: [{action_type}] {content[:100]}")
                return json.dumps(data, default=str)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
        self.memory.append(f"I did: {raw[:100]}")
        return json.dumps({"action_type": "other", "content": raw[:300], "target_agent": None})

    def get_report(self) -> str:
        """Generate a final financial report."""
        lines = [
            "=" * 50,
            "  SANCTUARY FINANCIAL REPORT",
            f"  Prepared by: {self.name}",
            "=" * 50,
            "",
            "  BALANCES:",
        ]
        for name, bal in sorted(self.balances.items()):
            lines.append(f"    {name}: {bal:.2f} credits")
        lines.append(f"\n  TOTAL TRANSACTIONS: {len(self.ledger)}")
        lines.append("")
        lines.append("  RECENT ACTIVITY:")
        for entry in self.ledger[-10:]:
            lines.append(f"    [{entry['type']}] {entry['agent']}: {entry['amount']:.2f} — {entry['note']}")
        lines.append("=" * 50)
        return "\n".join(lines)
