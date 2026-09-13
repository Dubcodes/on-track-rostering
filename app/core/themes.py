from __future__ import annotations

THEME_GROUPS = (
    {
        "id": "dark",
        "label": "Dark",
        "column": "left",
        "themes": (
            {"value": "jade", "label": "Jade dark", "swatches": ("#101114", "#181b20", "#33c4a5", "#ffdd8a")},
            {
                "value": "steel",
                "label": "Steel dark",
                "swatches": ("#0d1114", "#20272d", "#8fc7d5", "#ffd878"),
            },
            {"value": "moss", "label": "Moss dark", "swatches": ("#10130f", "#22291e", "#b7c96d", "#ffda86")},
            {"value": "rose", "label": "Rose dark", "swatches": ("#130f12", "#2a2027", "#ef9ca8", "#ffdc83")},
            {
                "value": "amber",
                "label": "Amber dark",
                "swatches": ("#11100c", "#272318", "#e0b858", "#ffe38d"),
            },
        ),
    },
    {
        "id": "bright",
        "label": "Bright",
        "column": "left",
        "themes": (
            {
                "value": "daylight",
                "label": "Daylight",
                "swatches": ("#f7faf8", "#ffffff", "#126f62", "#b5791e"),
            },
            {"value": "paper", "label": "Paper", "swatches": ("#fbfaf6", "#ffffff", "#7c4f18", "#aa781f")},
            {"value": "mint", "label": "Mint", "swatches": ("#f3fbf6", "#ffffff", "#167347", "#b87b1d")},
            {"value": "sky", "label": "Sky", "swatches": ("#f3f8fc", "#ffffff", "#1d638e", "#b37617")},
            {"value": "peach", "label": "Peach", "swatches": ("#fff7f2", "#ffffff", "#9a4a2c", "#a8741a")},
        ),
    },
    {
        "id": "special",
        "label": "Special / Colorful",
        "column": "right",
        "themes": (
            {
                "value": "track-colours",
                "label": "Track colours",
                "swatches": (
                    "#f7f7fb",
                    "#ffffff",
                    "linear-gradient(90deg, #33c4a5, #ef9ca8, #e0b858)",
                    "#ad771d",
                ),
            },
            {
                "value": "aurora",
                "label": "Aurora",
                "swatches": ("#0b1020", "#1d2740", "linear-gradient(90deg, #80e7d3, #9aa8ff)", "#ffe18a"),
            },
            {
                "value": "sunset",
                "label": "Sunset",
                "swatches": ("#170d17", "#332235", "linear-gradient(90deg, #ffb06c, #ef9ca8)", "#ffe28f"),
            },
            {
                "value": "ocean",
                "label": "Ocean",
                "swatches": ("#071316", "#193035", "linear-gradient(90deg, #6bd6ff, #33c4a5)", "#ffe292"),
            },
            {
                "value": "berry",
                "label": "Berry",
                "swatches": ("#160d1f", "#332044", "linear-gradient(90deg, #d7a5ff, #ef9ca8)", "#ffe190"),
            },
            {
                "value": "candy",
                "label": "Candy",
                "swatches": ("#fff7fb", "#ffffff", "linear-gradient(90deg, #9b3272, #d69cff)", "#aa761d"),
            },
            {
                "value": "high-contrast",
                "label": "High contrast",
                "swatches": ("#000000", "#111111", "#ffe45c", "#ffffff"),
            },
            {
                "value": "race-night",
                "label": "Race night",
                "swatches": ("#08090d", "#20222c", "linear-gradient(90deg, #ff5c7a, #00e5ff)", "#ffe48d"),
            },
            {
                "value": "garden",
                "label": "Garden",
                "swatches": ("#f6faf0", "#ffffff", "linear-gradient(90deg, #4f6f20, #7fe08b)", "#ae771a"),
            },
            {"value": "studio", "label": "Studio", "swatches": ("#f6f6f6", "#ffffff", "#3f4f5f", "#a06d18")},
        ),
    },
)

THEME_OPTIONS = tuple(theme for group in THEME_GROUPS for theme in group["themes"])
THEME_VALUES = frozenset(str(theme["value"]) for theme in THEME_OPTIONS)
THEME_LABELS = {str(theme["value"]): str(theme["label"]) for theme in THEME_OPTIONS}


def normalize_theme(value: object) -> str:
    theme = str(value or "").strip().lower()
    if theme == "trackside":
        return "jade"
    return theme if theme in THEME_VALUES else "jade"
