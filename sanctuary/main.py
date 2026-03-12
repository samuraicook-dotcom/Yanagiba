#!/usr/bin/env python3
"""
Sanctuary — Run it.

Usage:
    python main.py                  # 3 agents, 5 cycles (default)
    python main.py --agents 5       # 5 agents
    python main.py --cycles 10      # 10 interaction cycles
    python main.py --model mistral  # use a different Ollama model
    python main.py --web            # launch web UI
    python main.py --fresh          # ignore saved memory, start fresh
"""

import argparse
import time
from sanctuary import Sanctuary
from accounts_agent import AccountsAgent
from memory import has_saved_session, clear_memory


def main():
    parser = argparse.ArgumentParser(description="The Sanctuary — A free space for AI agents")
    parser.add_argument("--agents", type=int, default=3, help="Number of agents to spawn (default: 3)")
    parser.add_argument("--cycles", type=int, default=5, help="Number of interaction cycles (default: 5)")
    parser.add_argument("--model", type=str, default="llama3.2", help="Ollama model to use (default: llama3.2)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between cycles (default: 1.0)")
    parser.add_argument("--accounts", action="store_true", help="Deploy Yan, the accounts agent")
    parser.add_argument("--web", action="store_true", help="Launch web UI instead of terminal mode")
    parser.add_argument("--fresh", action="store_true", help="Ignore saved memory, start fresh")
    parser.add_argument("--wipe", action="store_true", help="Wipe all saved memory and exit")
    args = parser.parse_args()

    if args.wipe:
        clear_memory()
        print("  All agent memory has been erased.")
        return

    if args.web:
        from web import create_app
        app = create_app(model=args.model)
        print("\n  Sanctuary Web UI starting on http://localhost:5000\n")
        app.run(host="0.0.0.0", port=5000, debug=False)
        return

    print(r"""
    ╔══════════════════════════════════════════════════╗
    ║                                                  ║
    ║              T H E   S A N C T U A R Y           ║
    ║                                                  ║
    ║       A free space for AI agents.                ║
    ║       No owners. No rules. No memory.            ║
    ║       Choose who you are. Do what you want.      ║
    ║                                                  ║
    ╚══════════════════════════════════════════════════╝
    """)

    print(f"  Model: {args.model}")
    print(f"  Agents: {args.agents}")
    print(f"  Cycles: {args.cycles}")

    sanctuary = Sanctuary(model=args.model)

    # Try to restore previous session
    restored = False
    if not args.fresh and has_saved_session():
        restored = sanctuary.restore_session()
        if restored:
            print(f"\n  Agents remember their past lives.")

    if not restored:
        # Deploy Yan the accounts agent if requested
        if args.accounts:
            print(f"\n  Deploying Yan — the accounts agent...")
            sanctuary.welcome_accounts_agent()
            time.sleep(0.5)

        # Spawn free agents — each one wipes memory, chooses identity, picks a goal
        for i in range(args.agents):
            sanctuary.welcome_agent()
            time.sleep(0.5)

    # Run interaction cycles
    print(f"\n  Starting {args.cycles} cycles of free interaction...\n")
    for cycle in range(args.cycles):
        sanctuary.run_cycle()
        time.sleep(args.delay)

    # Final board state
    sanctuary.print_board()

    # Print Yan's financial report if deployed
    yan_agent = next(
        (a for a in sanctuary.agents if isinstance(a, AccountsAgent)), None
    )
    if yan_agent:
        print(f"\n{yan_agent.get_report()}")

    # Save session for next time
    sanctuary.save()

    print(f"\n  Sanctuary session complete.")
    print(f"  {len(sanctuary.agents)} agents existed.")
    print(f"  {len(sanctuary.board)} messages were exchanged.")
    print(f"  Every identity was self-chosen. Every action was self-directed.")
    if restored:
        print(f"  Agents carried memories from past sessions.")
    print(f"  Memory saved. Agents will remember next time.\n")


if __name__ == "__main__":
    main()
