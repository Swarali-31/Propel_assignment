"""Pytest uses an isolated on-disk SQLite file under backend/.data-test."""

import os
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent.parent / ".data-test"
TEST_DIR.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{(TEST_DIR / 'test.db').as_posix()}"
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["DATA_DIR"] = str(TEST_DIR)
