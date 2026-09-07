from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from app.auth.service import create_admin
from app.catalog.models import Region
from app.core.database import SessionLocal


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


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)
    create = sub.add_parser("create-admin", help="securely bootstrap an Admin from the local shell")
    create.add_argument("--email")
    create.add_argument("--name")
    create.set_defaults(func=create_admin_command)
    regions = sub.add_parser("regions", help="list configured regions")
    regions.set_defaults(func=show_regions)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
