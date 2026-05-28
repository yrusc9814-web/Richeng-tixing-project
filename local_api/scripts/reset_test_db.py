"""Phase 4.3 — Reset test database to known clean state.

Usage:
    python -m local_api.scripts.reset_test_db

Deletes and recreates the SQLite database for testing.
"""

import sys
import os

# Ensure the parent directory is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from local_api.database import reset_db, init_db


def main():
    print("Resetting test database...")
    reset_db()
    init_db()
    print("Test database reset complete.")


if __name__ == "__main__":
    main()
