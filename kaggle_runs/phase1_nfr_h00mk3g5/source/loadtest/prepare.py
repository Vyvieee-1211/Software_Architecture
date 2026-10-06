"""Create a fresh isolated fixture; never overwrite the user's concert.db."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def prepare(run_dir, users=200, tickets=150, random_seed=1211):
    run_dir = Path(run_dir).resolve()
    db_path = run_dir / "concert.db"
    if db_path.exists():
        raise FileExistsError(f"Refusing to reuse database: {db_path}")
    os.environ["DATABASE_URL"] = "sqlite:///" + db_path.as_posix()
    from app.repositories.database import SessionLocal, init_db, engine
    from app.repositories.models import Concert, TicketType
    from scripts.seed_loadtest_users import seed_users

    init_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    quantities = [tickets // 5, tickets * 3 // 10]
    quantities.append(tickets - sum(quantities))
    with SessionLocal.begin() as db:
        concert = Concert(name="HIT THE VIBE — Ticket Hunt", artist="Simulation",
                          venue="Test venue", start_time=now + timedelta(days=30),
                          sale_open_time=now - timedelta(hours=1))
        db.add(concert)
        db.flush()
        concert_id = concert.id
        for name, quantity, price in zip(("VIP", "Standard", "GA"), quantities, (3000000, 1500000, 800000)):
            db.add(TicketType(concert_id=concert.id, name=name, price=price,
                             total_quantity=quantity, remaining=quantity))
    engine.dispose()
    seed_users(db_path, users=max(100, users))
    manifest = {
        "scenario": "ticket-hunt-v2", "concert_id": concert_id, "total_tickets": tickets,
        "random_seed": random_seed,
        "accounts": [{"number": i, "email": f"kiemthugialap{i:03d}@gmail.com"} for i in range(1, users + 1)],
        "probe_accounts": [{"number": i, "email": f"kiemthugialap{i:03d}@gmail.com"} for i in range(1, max(100, users) + 1)],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("run_dir", type=Path)
    p.add_argument("--users", type=int, default=200)
    p.add_argument("--tickets", type=int, default=150)
    p.add_argument("--seed", type=int, default=1211)
    a = p.parse_args()
    if not 1 <= a.users <= 200 or a.tickets < 10:
        p.error("users must be 1..200 and tickets >= 10")
    prepare(a.run_dir, a.users, a.tickets, a.seed)
