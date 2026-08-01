"""
run_local.py — Run the actor locally without Apify CLI.

Usage:
    python run_local.py

Reads input from storage/key_value_stores/default/INPUT.json
"""
import asyncio
import os
from pathlib import Path

os.environ.setdefault("APIFY_LOCAL_STORAGE_DIR", str(Path(__file__).parent / "storage"))

from my_actor.main import main

if __name__ == "__main__":
    Path("storage/key_value_stores/default").mkdir(parents=True, exist_ok=True)
    asyncio.run(main())
