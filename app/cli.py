from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from app.auth.service import create_admin
from app.catalog.models import Region
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.housekeeping.service import terminal_housekeeping
from app.notifications.service import generate_periodic_digests, generate_reminders, process_pending
from app.operations.readiness import evaluate_readiness


def create_admin_command(args: argparse.Namespace) -> int:
    email = args.email or input("Admin email: ").strip()
    name = args.name or input("Display name: ").strip()
    secret = getpass.getpass("Admin password or 8+ digit PIN: ")
    confirmation = getpass.getpass("Confirm credential: ")
    if secret != confirmation:
        raise SystemExit("Credentials did not match.")
    with SessionLocal() as db:
        user = create_admin(db, email, name, secret)
    print(f"Created Admin {user.display_name} ({user.email}).")
    return 0


def show_regions(_: argparse.Namespace) -> int:
    with SessionLocal() as db:
        for region in db.scalars(select(Region).order_by(Region.name)):
            print(f"{region.id}  {region.name}  {region.lifecycle}")
    return 0


def deliver_notifications_command(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        generated = generate_reminders(db)
        digests = generate_periodic_digests(db)
        count = process_pending(db, limit=args.limit)
    print(
        f"Generated {generated} reminder(s) and {digests} digest(s); processed {count} notification event(s)."
    )
    return 0


def housekeeping_command(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        result = terminal_housekeeping(
            db,
            retention_days=get_settings().retention_days,
            apply=args.apply,
        )
    mode = "APPLIED" if result.applied else "DRY RUN"
    print(f"{mode} retention cutoff {result.cutoff.isoformat()}")
    for name, count in result.counts.items():
        print(f"{name}: {count}")
    return 0


def production_check_command(_: argparse.Namespace) -> int:
    with SessionLocal() as db:
        result = evaluate_readiness(db)
    for check in result.checks:
        print(f"{check.status}: {check.name} — {check.message}")
    return 0 if result.ready else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)
    create = sub.add_parser("create-admin", help="securely bootstrap an Admin from the local shell")
    create.add_argument("--email")
    create.add_argument("--name")
    create.set_defaults(func=create_admin_command)
    regions = sub.add_parser("regions", help="list configured regions")
    regions.set_defaults(func=show_regions)
    deliver = sub.add_parser("deliver-notifications", help="process pending Web Push outbox events")
    deliver.add_argument("--limit", type=int, default=50)
    deliver.set_defaults(func=deliver_notifications_command)
    housekeeping = sub.add_parser(
        "housekeeping", help="report or remove expired terminal operational records"
    )
    housekeeping.add_argument(
        "--apply", action="store_true", help="delete the records reported by the dry run"
    )
    housekeeping.add_argument(
        "--dry-run", action="store_true", help="report eligible records without deleting them"
    )
    housekeeping.set_defaults(func=housekeeping_command)
    production_check = sub.add_parser(
        "production-check", help="evaluate production configuration and runtime readiness"
    )
    production_check.set_defaults(func=production_check_command)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
