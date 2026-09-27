from __future__ import annotations

TRANSPORT_UNASSIGNED = "UNASSIGNED"
TRANSPORT_VEHICLE = "VEHICLE"
TRANSPORT_SELF = "SELF_TRAVEL"
TRANSPORT_NOT_REQUIRED = "NOT_REQUIRED"
TRANSPORT_CUSTOM = "CUSTOM"

TRANSPORT_MODES = {
    TRANSPORT_UNASSIGNED,
    TRANSPORT_VEHICLE,
    TRANSPORT_SELF,
    TRANSPORT_NOT_REQUIRED,
    TRANSPORT_CUSTOM,
}

TRANSPORT_LABELS = {
    TRANSPORT_UNASSIGNED: "No transport assigned yet",
    TRANSPORT_VEHICLE: "Vehicle",
    TRANSPORT_SELF: "Making own way",
    TRANSPORT_NOT_REQUIRED: "No transport required",
    TRANSPORT_CUSTOM: "Custom transport",
}


def transport_display(mode: str, vehicle_name: str | None, custom_text: str) -> str:
    if mode == TRANSPORT_VEHICLE:
        return vehicle_name or TRANSPORT_LABELS[TRANSPORT_UNASSIGNED]
    if mode == TRANSPORT_CUSTOM:
        return custom_text.strip() or TRANSPORT_LABELS[TRANSPORT_UNASSIGNED]
    return TRANSPORT_LABELS.get(mode, TRANSPORT_LABELS[TRANSPORT_UNASSIGNED])
