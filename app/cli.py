from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from app.auth.service import create_admin
from app.catalog.models import Region
from app.core.database import SessionLocal
from app.notifications.service import generate_reminders, process_pending


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
        count = process_pending(db, limit=args.limit)
    print(f"Generated {generated} reminder(s); processed {count} notification event(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)
    create = sub.add_parser("create-admin", help="securely bootstrap an Admin from the local shell")
    create.add_argument("--email")
    create.add_argument("--name")
    create.set_defaults(func=create_admin_command)
    regions = sub.add_parser("regions", help="list configured regions")
    regions.set_defaults(func=show_regions)
    deliver = sub.add_parser(
        "deliver-notifications", help="process pending Web Push outbox events"
    )
    deliver.add_argument("--limit", type=int, default=50)
    deliver.set_defaults(func=deliver_notifications_command)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
