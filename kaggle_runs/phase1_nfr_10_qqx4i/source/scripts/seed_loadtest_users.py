"""Add 200 simulation accounts to an existing database without changing other data.

Run: python -m scripts.seed_loadtest_users --db concert.db
Each account's password is its full email address. Existing accounts are preserved.
"""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

import bcrypt


def seed_users(db_path: Path, users: int = 200) -> None:
    db_path = db_path.resolve(strict=True)
    emails = [f"kiemthugialap{i:03d}@gmail.com" for i in range(1, users + 1)]
    placeholders = ",".join("?" for _ in emails)
    with closing(sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)) as db:
        existing = dict(db.execute(
            f"SELECT email, password_hash FROM users WHERE email IN ({placeholders})",
            emails,
        ))

    for email, password_hash in existing.items():
        if not bcrypt.checkpw(email.encode(), password_hash.encode()):
            raise ValueError(f"Existing account has a different password: {email}")

    # Compute hashes before opening a write transaction.
    rows = []
    for number, email in enumerate(emails, start=1):
        if email not in existing:
            rows.append((
                email,
                bcrypt.hashpw(email.encode(), bcrypt.gensalt()).decode(),
                f"Simulation User {number:03d}",
            ))
        if number % 50 == 0:
            print(f"Prepared {number}/{users} accounts", flush=True)

    with closing(sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True)) as db:
        with db:
            db.executemany(
                "INSERT INTO users (email, password_hash, full_name) VALUES (?, ?, ?)",
                rows,
            )
        saved = dict(db.execute(
            f"SELECT email, password_hash FROM users WHERE email IN ({placeholders})",
            emails,
        ))

    if set(saved) != set(emails):
        raise RuntimeError("Account verification failed")
    expected = {**existing, **{email: hashed for email, hashed, _ in rows}}
    if saved != expected:
        raise RuntimeError("Stored password hashes do not match")
    print(f"Database: {db_path}")
    print(f"Created: {len(rows)}; already present: {len(existing)}; verified: {len(saved)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("concert.db"))
    seed_users(parser.parse_args().db)
