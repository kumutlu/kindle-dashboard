#!/usr/bin/env python3
"""Theme registry and visibility policy for the Kindle dashboard."""

VISIBILITY_FIELDS = (
    "show_weather",
    "show_forecast",
    "show_server",
    "show_pihole",
    "show_tailscale",
)

THEMES = {
    "home_dashboard": {
        "label": "Home Dashboard",
        "description": "Weather and home server overview",
        "implemented": True,
    },
    "minimal_weather": {
        "label": "Minimal Weather",
        "description": "Large current weather and forecast",
        "implemented": True,
    },
    "server_monitor": {
        "label": "Server Monitor",
        "description": "Focused system and network status",
        "implemented": True,
    },
    "maarif_calendar": {
        "label": "Maarif Calendar",
        "name": "Maarif Calendar",
        "description": "Traditional Turkish Maarif calendar style dashboard",
        "category": "calendar / lifestyle",
        "status": "active",
        "implemented": True,
    },
    "family_dashboard": {
        "label": "Family Dashboard",
        "description": "Compact weather with daily family reminders",
        "implemented": True,
    },
    "todo": {
        "label": "Todo",
        "description": "Per-device task list",
        "implemented": True,
    },
}

THEME_ALIASES = {
    "travel_weather": "minimal_weather",
    "compact_dashboard": "home_dashboard",
}

WEATHER_ONLY = {
    "show_weather": True,
    "show_forecast": True,
    "show_server": False,
    "show_pihole": False,
    "show_tailscale": False,
}

SERVER_ONLY = {
    "show_weather": False,
    "show_forecast": False,
    "show_server": True,
    "show_pihole": True,
    "show_tailscale": True,
}


def validate_theme(theme):
    theme = THEME_ALIASES.get(theme, theme)
    definition = THEMES.get(theme)
    if definition is None:
        raise ValueError("unsupported theme")
    if not definition["implemented"]:
        raise ValueError("theme is not implemented yet")
    return theme


def effective_visibility(theme, config):
    theme = validate_theme(theme)
    if theme == "home_dashboard":
        return {key: config[key] for key in VISIBILITY_FIELDS}
    if theme == "family_dashboard":
        return {
            "show_weather": config.get("show_weather", True),
            "show_forecast": config.get("show_forecast", True),
            "show_server": False,
            "show_pihole": False,
            "show_tailscale": False,
        }
    if theme in ("minimal_weather", "maarif_calendar"):
        return dict(WEATHER_ONLY)
    if theme == "server_monitor":
        return dict(SERVER_ONLY)
    if theme == "todo":
        return {key: False for key in VISIBILITY_FIELDS}
    raise ValueError("unsupported theme")
