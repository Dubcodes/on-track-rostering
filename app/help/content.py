from __future__ import annotations

from dataclasses import dataclass

from app.auth.policy import Actor
from app.core.enums import Role


@dataclass(frozen=True)
class HelpLink:
    label: str
    href: str
    capability: str = "everyone"


@dataclass(frozen=True)
class HelpTopic:
    title: str
    introduction: str
    tasks: tuple[str, ...]
    rules: tuple[str, ...]
    links: tuple[HelpLink, ...] = ()


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
    "open": HelpLink("Open positions", "/open-positions", "person"),
}


TOPICS = {
    "month": HelpTopic("Your Month", "Month shows published roster and planning information you are allowed to see.", ("Switch between Month and List.", "Open a published Workday or external racing event.", "Use calendar preferences in Settings."), ("Private drafts never appear here.", "Racing-source preferences never hide your published assignment."), (LINKS["month"], LINKS["settings"])),
    "day": HelpTopic("Published Workday", "Day is the authoritative published operational view.", ("Review timing, crew and public notes.", "Open source evidence when linked.", "Use available self-service actions before your shift."), ("Private notes stay restricted.", "Published revisions remain immutable snapshots."), (LINKS["month"],)),
    "builder": HelpTopic("Build a roster", "Build works in a private draft until Preview and Publish.", ("Choose Position and Person, including Open or TBC.", "Use More for supported timing and note controls.", "Save & Preview, review changes, then Publish."), ("Employees cannot see the draft.", "Publishing atomically replaces the visible revision."), (LINKS["build"], LINKS["month"])),
    "settings": HelpTopic("Settings", "Settings collects personal preferences, workspaces and account security.", ("Choose calendar display and notification preferences.", "Manage trusted devices, passkeys and MFA.", "Change your credential or open an authorized workspace."), ("Calendar preferences do not change published roster data.", "Use a fresh sign-in for sensitive account changes."), (LINKS["settings"], LINKS["month"], LINKS["build"], LINKS["catalog"])),
    "admin": HelpTopic("Administration", "Administration controls global product and foundation setup.", ("Manage branding and operational settings.", "Edit Regions and Tracks or open Accounts.", "Use Online Sources and Data Import for controlled setup."), ("Archive records with history.", "Remove unused is only for unreferenced setup mistakes."), (LINKS["admin"], LINKS["catalog"], LINKS["accounts"], LINKS["sources"], LINKS["import"])),
    "catalog": HelpTopic("Master data", "Master data defines operational Regions, Tracks, Crew Groups and Base Positions.", ("Edit policies, geography and Track map references.", "Archive inactive records and restore them when needed.", "Remove only records with no business references."), ("Lifecycle changes never rewrite published snapshots.", "Track palette slots are allocated automatically."), (LINKS["catalog"], LINKS["admin"])),
    "crew": HelpTopic("Crew calendar", "Crew shows published regional work for authorized teams.", ("Move between dates and open published Day detail.", "Use the management workspace when roster changes are required."), ("Crew reads published revisions only.", "Region visibility remains role-based."), (LINKS["crew"], LINKS["month"], LINKS["build"])),
    "hours": HelpTopic("Hours", "Hours summarizes published personal participation; it is not payroll.", ("Review fortnight totals and Workday timing.", "Open the published Day when details need checking."), ("No automatic unpaid-break deduction is performed.", "Raise corrections with a Manager."), (LINKS["hours"], LINKS["month"])),
    "accounts": HelpTopic("Accounts and invitations", "Accounts are authentication identities and remain separate from rosterable People.", ("Create or invite an account.", "Link the correct Person and grant regional capabilities.", "Review active devices and account status."), ("Public signup grants no roster access.", "Admin credentials have stronger requirements."), (LINKS["accounts"], LINKS["admin"])),
    "data-import": HelpTopic("Data Import", "Data Import loads the strict provider-neutral On Track JSON format.", ("Paste or upload, then inspect Preview.", "Resolve conflicts and ambiguous People.", "Apply the validated bundle transactionally."), ("Preview performs zero writes.", "Credentials are never imported; mapping conflicts block Apply."), (LINKS["import"], LINKS["catalog"], LINKS["sources"])),
    "online-sources": HelpTopic("Online Sources", "Racing sources are planning evidence; manual rostering always remains available.", ("Review provider health separately from mapping backlog.", "Confirm a suggested Track or use Create Track & Map.", "Download the grouped mapping template."), ("Suggestions never map automatically.", "CLUB_ONLY means the physical venue is unconfirmed."), (LINKS["sources"], LINKS["import"], LINKS["catalog"])),
    "open-positions": HelpTopic("Open positions", "Open positions are published roster slots accepting Employee applications.", ("Review suitable published opportunities.", "Apply or withdraw while the position remains available."), ("Applications never edit the published roster directly.", "A Manager makes the final selection."), (LINKS["open"], LINKS["month"])),
}


INDEX = HelpTopic(
    "Help home",
    "Choose the workspace you are using. Help links are filtered to your current authority.",
    ("Your roster: Month, Day, Hours and Open positions.", "Management: Build, Crew, Master data and Accounts.", "Administration: system setup, sources and import.", "Account and security: Settings, credentials, trusted devices and MFA."),
    ("On Track is the authoritative roster.", "Ask a Manager when published operational information looks wrong."),
    tuple(LINKS.values()),
)


def _allowed(actor: Actor, capability: str) -> bool:
    roles = actor.global_roles | frozenset(
        role for regional_roles in actor.regional_roles.values() for role in regional_roles
    )
    if capability == "everyone":
        return True
    if capability == "person":
        return actor.person_id is not None
    if capability == "admin":
        return actor.is_admin
    if capability == "manage":
        return actor.is_admin or bool(roles & {Role.MANAGER.value, Role.SUB_MANAGER.value})
    if capability in {"catalog", "accounts"}:
        return actor.is_admin or Role.MANAGER.value in roles
    if capability == "crew":
        return actor.is_admin or bool(roles & {Role.EMPLOYEE.value, Role.SUB_MANAGER.value, Role.MANAGER.value, Role.VIEWER.value})
    return False


def help_topic(context_key: str, actor: Actor) -> tuple[str, HelpTopic, tuple[HelpLink, ...]]:
    key = context_key.strip().casefold().replace("_", "-")
    topic = TOPICS.get(key, INDEX)
    return (key if key in TOPICS else "index"), topic, tuple(
        link for link in topic.links if _allowed(actor, link.capability)
    )
