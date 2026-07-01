"""
run_local.py — Run the actor locally without Apify CLI.

Usage:
    python run_local.py

Reads input from storage/key_value_stores/default/INPUT.json
(same path the Apify CLI uses).
"""
import asyncio
import json
import os
from pathlib import Path

# Simulate Apify local storage layout
os.environ.setdefault("APIFY_LOCAL_STORAGE_DIR", str(Path(__file__).parent / "storage"))

from my_actor.main import main

if __name__ == "__main__":
    # Write a sample INPUT.json if it doesn't exist
    input_path = Path("storage/key_value_stores/default/INPUT.json")
    input_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        sample = {
            "start_urls": [
                "https://www.indeed.com/jobs?q=python+developer&l=Remote"
            ],
            "about_me": "Python Developer\nBackend Engineer\nDjango Developer",
            "max_jobs": 10,
            "per_company_jobs": 2,
            "min_match_percentage": 30,
            "concurrency": 2,
            "ignore_companies": "",
            "ignore_related": "",
            "google_sheet_url": "",
            "sheet_name": "Indeed Jobs",
            "headless": False,
        }
        input_path.write_text(json.dumps(sample, indent=2))
        print(f"📝 Created sample input at {input_path}")

    asyncio.run(main())
