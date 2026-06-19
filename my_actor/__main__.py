"""
src/__main__.py — allows `python -m src` to work.
"""
import asyncio
from .main import main

asyncio.run(main())
