"""CLI for seeding tenants/users and promoting a LINE user to admin.

Examples:
    python seed.py init-db
    python seed.py add-tenant --id U123abc... --name "Friend 1 Math Group" \\
        --channel-secret xxxx --channel-access-token yyyy --folder-path ./materials/friend1
    python seed.py add-tenant --from-env --id U123abc... --name "Friend 1 Math Group"
    python seed.py add-tenant --id U123abc... --name "Friend 1 Math Group" \\
        --google-calendar-id teacher@gmail.com --google-drive-folder-id 1AbCdEfGh...
    python seed.py set-admin --tenant-id U123abc... --line-user-id Uxyz...
    python seed.py list
"""

import argparse
import asyncio

from sqlmodel import select

from app.config import LINE_DEFAULT_CHANNEL_ACCESS_TOKEN, LINE_DEFAULT_CHANNEL_SECRET
from app.db import async_session, init_db
from app.models import Tenant, User, UserRole


async def cmd_init_db(_args: argparse.Namespace) -> None:
    await init_db()
    print("Database tables created.")


async def cmd_add_tenant(args: argparse.Namespace) -> None:
    channel_secret = args.channel_secret
    channel_access_token = args.channel_access_token
    if args.from_env:
        channel_secret = channel_secret or LINE_DEFAULT_CHANNEL_SECRET
        channel_access_token = channel_access_token or LINE_DEFAULT_CHANNEL_ACCESS_TOKEN

    if not channel_secret or not channel_access_token:
        raise SystemExit(
            "channel-secret and channel-access-token are required "
            "(pass them directly or use --from-env with LINE_CHANNEL_SECRET/"
            "LINE_CHANNEL_ACCESS_TOKEN set)"
        )

    await init_db()
    async with async_session() as session:
        tenant = await session.get(Tenant, args.id)
        if tenant is not None:
            print(f"Tenant {args.id} already exists, updating fields.")
            tenant.name = args.name
            tenant.channel_secret = channel_secret
            tenant.channel_access_token = channel_access_token
            # Only overwrite optional fields if explicitly passed, so
            # re-running add-tenant for LINE credential rotation doesn't
            # clear previously-set values.
            if args.folder_path is not None:
                tenant.folder_path = args.folder_path
            if args.google_calendar_id is not None:
                tenant.google_calendar_id = args.google_calendar_id
            if args.google_drive_folder_id is not None:
                tenant.google_drive_folder_id = args.google_drive_folder_id
        else:
            tenant = Tenant(
                id=args.id,
                name=args.name,
                channel_secret=channel_secret,
                channel_access_token=channel_access_token,
                folder_path=args.folder_path,
                google_calendar_id=args.google_calendar_id,
                google_drive_folder_id=args.google_drive_folder_id,
            )
        session.add(tenant)
        await session.commit()
        print(f"Tenant saved: id={tenant.id} name={tenant.name!r}")


async def cmd_set_admin(args: argparse.Namespace) -> None:
    await init_db()
    async with async_session() as session:
        result = await session.exec(
            select(User).where(
                User.tenant_id == args.tenant_id, User.line_user_id == args.line_user_id
            )
        )
        user = result.first()
        if user is None:
            user = User(
                tenant_id=args.tenant_id,
                line_user_id=args.line_user_id,
                role=UserRole.admin,
            )
            print("User did not exist yet; creating as admin.")
        else:
            user.role = UserRole.admin
            print(f"Promoting existing user id={user.id} to admin.")
        session.add(user)
        await session.commit()
        print(f"User is now admin: tenant={user.tenant_id} line_user_id={user.line_user_id}")


async def cmd_list(_args: argparse.Namespace) -> None:
    await init_db()
    async with async_session() as session:
        tenants = (await session.exec(select(Tenant))).all()
        print("Tenants:")
        for t in tenants:
            print(
                f"  - {t.id}  name={t.name!r}  folder_path={t.folder_path!r}  "
                f"google_calendar_id={t.google_calendar_id!r}  "
                f"google_drive_folder_id={t.google_drive_folder_id!r}"
            )

        users = (await session.exec(select(User))).all()
        print("Users:")
        for u in users:
            print(
                f"  - tenant={u.tenant_id} line_user_id={u.line_user_id} "
                f"role={u.role.value} display_name={u.display_name!r}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed/manage pj's tenant & user data")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create database tables").set_defaults(func=cmd_init_db)

    p_add_tenant = sub.add_parser("add-tenant", help="Create or update a tenant")
    p_add_tenant.add_argument("--id", required=True, help="LINE destination ID for this tenant")
    p_add_tenant.add_argument("--name", required=True)
    p_add_tenant.add_argument("--channel-secret")
    p_add_tenant.add_argument("--channel-access-token")
    p_add_tenant.add_argument("--folder-path", default=None)
    p_add_tenant.add_argument(
        "--google-calendar-id",
        default=None,
        help="Google Calendar ID (e.g. teacher@gmail.com) shared with the service account",
    )
    p_add_tenant.add_argument(
        "--google-drive-folder-id",
        default=None,
        help="Google Drive folder ID shared with the service account",
    )
    p_add_tenant.add_argument(
        "--from-env",
        action="store_true",
        help="Fall back to LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN from .env",
    )
    p_add_tenant.set_defaults(func=cmd_add_tenant)

    p_set_admin = sub.add_parser("set-admin", help="Promote a LINE user to admin for a tenant")
    p_set_admin.add_argument("--tenant-id", required=True)
    p_set_admin.add_argument("--line-user-id", required=True)
    p_set_admin.set_defaults(func=cmd_set_admin)

    sub.add_parser("list", help="List tenants and users").set_defaults(func=cmd_list)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
