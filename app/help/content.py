from __future__ import annotations

from dataclasses import dataclass, replace

from app.auth.policy import Actor
from app.core.enums import Role


@dataclass(frozen=True)
class HelpLink:
    label: str
    href: str
    capability: str = "everyone"


@dataclass(frozen=True)
class HelpItem:
    text: str
    capability: str = "everyone"


@dataclass(frozen=True)
class HelpTopic:
    title: str
    introduction: str
    tasks: tuple[str | HelpItem, ...]
    rules: tuple[str | HelpItem, ...]
    links: tuple[HelpLink, ...] = ()
    capability: str = "everyone"


LINKS = {
    "month": HelpLink("Month", "/month"),
    "settings": HelpLink("Settings", "/settings"),
    "build": HelpLink("Build roster", "/manage/workdays/new", "manage"),
    "catalog": HelpLink("Master data", "/manage/catalog", "catalog"),
    "admin": HelpLink("Administration", "/admin", "admin"),
    "sources": HelpLink("Online Sources", "/admin/online-sources", "admin"),
    "import": HelpLink("Data Import", "/admin/data-import", "admin"),
    "accounts": HelpLink("Accounts", "/manage/accounts", "accounts"),
    "crew": HelpLink("Crew calendar", "/crew", "crew"),
    "hours": HelpLink("Hours", "/hours", "person"),
    "open": HelpLink("Open positions", "/open-positions", "employee"),
}


TOPICS = {
    "month": HelpTopic(
        "Your Month",
        "Month shows your published roster and the planning information available to you.",
        (
            "Switch between Month and List.",
            "Open a Workday to see its published details.",
            "Choose calendar display options in Settings.",
        ),
        (
            "Draft roster changes are not shown until they are published.",
            "Racing calendar preferences never hide your published assignment.",
        ),
        (LINKS["month"], LINKS["settings"]),
    ),
    "day": HelpTopic(
        "Published Workday",
        "Day shows the roster and timing that crew should work from.",
        (
            "Check timing, crew and public notes.",
            "Open linked racing details when they are useful.",
            "Use any available response actions before your shift.",
        ),
        (
            "Private notes stay visible only to the named crew member and authorized management.",
            "Ask a Manager if published information needs changing.",
        ),
        (LINKS["month"],),
    ),
    "builder": HelpTopic(
        "Build a roster",
        "Build keeps changes private while you prepare and review a roster.",
        (
            "Choose each Position and crew member, or leave a Position Open or Unassigned.",
            "Use More for personal timing, travel and notes.",
            "Choose Save & Preview, check the changes, then Publish.",
        ),
        (
            "Crew cannot see changes while they remain a draft.",
            "Publishing replaces the roster crew can see.",
        ),
        (LINKS["build"], LINKS["month"]),
        "manage",
    ),
    "settings": HelpTopic(
        "Settings",
        "Settings contains your display choices, notifications and account security.",
        (
            "Choose calendar and notification preferences.",
            "Manage trusted devices, passkeys and extra sign-in security.",
            "Change your PIN or password when needed.",
        ),
        (
            "Display preferences do not change roster data.",
            "Sensitive account changes may ask you to sign in again.",
        ),
        (LINKS["settings"], LINKS["month"]),
    ),
    "management": HelpTopic(
        "Management",
        "Management brings together the roster and crew tools you are allowed to use.",
        (
            "Build and preview roster changes before publishing.",
            HelpItem("Review regional crew, notices and Team Hours.", "crew"),
            HelpItem("Open account or master-data tools when your authority allows it.", "catalog"),
        ),
        (
            "Your available Regions and actions come from your account permissions.",
            "Published rosters remain unchanged until you deliberately publish.",
        ),
        capability="manage",
    ),
    "crew-management": HelpTopic(
        "Manage Crew",
        "Crew management helps you maintain the people and access details within your authority.",
        (
            "Search for a person and open their details.",
            "Update roster details and Position capability decisions.",
            HelpItem(
                "Complete work email and Primary Region before creating a setup link.",
                "accounts",
            ),
        ),
        (
            "Accounts and rosterable people are separate records.",
            "Managers cannot control Manager or Admin account security.",
        ),
        capability="manage",
    ),
    "admin": HelpTopic(
        "Administration",
        "Administration contains global setup and deployment information for Admins.",
        (
            "Manage branding and operational settings.",
            "Review Regions, Tracks and account administration.",
            "Use Online Sources and Data Import for controlled setup.",
        ),
        (
            "Archive records that have history.",
            "Remove unused records only when they have no business references.",
        ),
        (LINKS["admin"], LINKS["catalog"], LINKS["accounts"], LINKS["sources"], LINKS["import"]),
        "admin",
    ),
    "catalog": HelpTopic(
        "Master data",
        "Master data contains the Regions, Tracks, Crew Groups, Positions and Vehicles used by rostering.",
        (
            "Maintain operational locations and roster choices.",
            "Archive inactive records and restore them when needed.",
            "Remove only unused setup mistakes.",
        ),
        (
            "Changes do not rewrite published rosters.",
            "Vehicle borrowing does not change its owning Region.",
        ),
        (LINKS["catalog"], LINKS["admin"]),
        "catalog",
    ),
    "crew": HelpTopic(
        "Crew calendar",
        "Crew shows the published regional roster you are permitted to view.",
        (
            "Move between dates and open a published Day.",
            HelpItem("Use Management when roster changes are required.", "manage"),
        ),
        (
            "Crew shows published rosters only.",
            "Regional visibility follows your account permissions.",
        ),
        (LINKS["crew"], LINKS["month"], LINKS["build"]),
        "crew",
    ),
    "hours": HelpTopic(
        "Hours",
        "Hours summarizes published roster time; it is not payroll.",
        (
            "Review fortnight totals and Workday timing.",
            "Open the published Day when details need checking.",
        ),
        (
            "Hours are not reduced automatically for breaks.",
            "Raise corrections with a Manager.",
        ),
        (LINKS["hours"], LINKS["month"]),
        "person",
    ),
    "accounts": HelpTopic(
        "Accounts and invitations",
        "Accounts control sign-in and access; Crew records identify people on the roster.",
        (
            "Create or invite an account for the correct person.",
            "Grant only the regional access the person needs.",
            "Review account status and active devices.",
        ),
        (
            "Public signup does not grant roster access.",
            "Admin accounts require stronger credentials.",
        ),
        (LINKS["accounts"], LINKS["admin"]),
        "accounts",
    ),
    "data-import": HelpTopic(
        "Data Import",
        "Data Import previews structured setup data before anything is applied.",
        (
            "Paste or upload the file, then inspect Preview.",
            "Resolve conflicts and unclear people.",
            "Apply only after the complete Preview is understood.",
        ),
        (
            "Preview makes no changes.",
            "Credentials are never imported, and mapping conflicts stop Apply.",
        ),
        (LINKS["import"], LINKS["catalog"], LINKS["sources"]),
        "admin",
    ),
    "online-sources": HelpTopic(
        "Online Sources",
        "Racing sources provide planning information; manual rostering always remains available.",
        (
            "Review provider health separately from Track mapping.",
            "Confirm suggested Tracks or create the correct Track and mapping.",
            "Download the grouped mapping template when useful.",
        ),
        (
            "Suggestions never map themselves.",
            "A club-only match does not confirm the physical venue.",
        ),
        (LINKS["sources"], LINKS["import"], LINKS["catalog"]),
        "admin",
    ),
    "open-positions": HelpTopic(
        "Open positions",
        "Open positions are published roster vacancies where Employees can offer to work.",
        (
            "Review suitable opportunities.",
            "Apply or withdraw while the Position remains open.",
        ),
        (
            "Applying does not change the published roster.",
            "A Manager makes the final selection.",
        ),
        (LINKS["open"], LINKS["month"]),
        "employee",
    ),
}


INDEX = HelpTopic(
    "Help",
    "Use this short guide to find the roster information and actions available to you.",
    (
        HelpItem("Check your published Month and Day roster.", "person"),
        HelpItem("Review your rostered time in Hours.", "person"),
        HelpItem("Apply for a suitable Open Position.", "employee"),
        HelpItem("View the regional Crew calendar.", "crew"),
        HelpItem("Build rosters and manage the crew tools available to you.", "manage"),
        HelpItem("Use Administration for global setup, online sources and imports.", "admin"),
        "Use Settings for notifications and account security.",
    ),
    (
        "Use the published roster as the source of truth.",
        "Report anything that looks wrong so it can be checked.",
    ),
    tuple(LINKS.values()),
)


ALIASES = {
    "general": "index",
    "crew-management": "crew-management",
    "management": "management",
}


def _roles(actor: Actor) -> frozenset[str]:
    return actor.global_roles | frozenset(
        role for regional_roles in actor.regional_roles.values() for role in regional_roles
    )


def _allowed(actor: Actor, capability: str) -> bool:
    roles = _roles(actor)
    if capability == "everyone":
        return True
    if capability == "person":
        return actor.person_id is not None
    if capability == "employee":
        return bool(roles & {Role.EMPLOYEE.value, Role.CASUAL.value})
    if capability == "admin":
        return actor.is_admin
    if capability == "manage":
        return actor.is_admin or bool(roles & {Role.MANAGER.value, Role.SUB_MANAGER.value})
    if capability in {"catalog", "accounts"}:
        return actor.is_admin or Role.MANAGER.value in roles
    if capability == "crew":
        return actor.is_admin or bool(
            roles
            & {
                Role.EMPLOYEE.value,
                Role.CASUAL.value,
                Role.SUB_MANAGER.value,
                Role.MANAGER.value,
                Role.VIEWER.value,
            }
        )
    return False


def help_access(actor: Actor) -> dict[str, bool]:
    return {
        capability: _allowed(actor, capability)
        for capability in (
            "person",
            "employee",
            "crew",
            "manage",
            "catalog",
            "accounts",
            "admin",
        )
    }


def _resolve_items(items: tuple[str | HelpItem, ...], actor: Actor) -> tuple[str, ...]:
    return tuple(
        item if isinstance(item, str) else item.text
        for item in items
        if isinstance(item, str) or _allowed(actor, item.capability)
    )


def help_topic(context_key: str, actor: Actor) -> tuple[str, HelpTopic, tuple[HelpLink, ...]]:
    requested = context_key.strip().casefold().replace("_", "-")
    key = ALIASES.get(requested, requested)
    topic = TOPICS.get(key)
    if topic is None or not _allowed(actor, topic.capability):
        key, topic = "index", INDEX
    resolved = replace(
        topic,
        tasks=_resolve_items(topic.tasks, actor),
        rules=_resolve_items(topic.rules, actor),
    )
    return key, resolved, tuple(
        link for link in resolved.links if _allowed(actor, link.capability)
    )
