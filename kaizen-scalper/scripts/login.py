"""
scripts/login.py — Zerodha Kite Connect Daily Login

Run this once every morning before market open to generate a fresh access token.
Reads KITE_API_KEY and KITE_API_SECRET from .env and saves KITE_ACCESS_TOKEN back.

Usage:
    python scripts/login.py
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv, set_key


def main() -> None:
    """Interactive login flow for Zerodha Kite Connect."""
    env_path = Path(".env")
    load_dotenv(env_path)

    api_key = os.environ.get("KITE_API_KEY", "")
    api_secret = os.environ.get("KITE_API_SECRET", "")

    if not api_key or not api_secret:
        print("ERROR: KITE_API_KEY and KITE_API_SECRET must be set in .env")
        sys.exit(1)

    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("ERROR: kiteconnect not installed. Run: pip install kiteconnect")
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    print("\n" + "=" * 60)
    print("  KAIZEN SCALPER — Zerodha Login")
    print("=" * 60)
    print(f"\n1. Open this URL in your browser:\n\n   {login_url}\n")
    print("2. Log in with your Zerodha credentials.")
    print("3. After redirect, copy the full URL from your browser.")
    print("   It looks like: https://127.0.0.1/?request_token=XXXXX&action=login&status=success")
    print()

    request_token = input("4. Paste the request_token value here: ").strip()

    if not request_token:
        print("ERROR: No request token provided.")
        sys.exit(1)

    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
        user_name = data.get("user_name", "?")

        # Save to .env
        if not env_path.exists():
            env_path.touch()
        set_key(str(env_path), "KITE_ACCESS_TOKEN", access_token)

        print(f"\n✅ Login successful! Welcome, {user_name}")
        print(f"   Access token saved to .env")
        print("=" * 60 + "\n")

    except Exception as exc:
        print(f"\nERROR: Failed to generate session: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
