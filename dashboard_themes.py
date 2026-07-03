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
    "travel_weather": {
        "label": "Travel Weather",
        "description": "Location-focused weather for trips",
        "implemented": True,
    },
    "maarif_calendar": {
        "label": "Maarif Calendar",
        "description": "Coming soon",
        "implemented": False,
    },
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
    definition = THEMES.get(theme)
    if definition is None:
        raise ValueError("unsupported theme")
    if not definition["implemented"]:
        raise ValueError("theme is not implemented yet")
    return theme


def effective_visibility(theme, config):
    validate_theme(theme)
    if theme == "home_dashboard":
        return {key: config[key] for key in VISIBILITY_FIELDS}
    if theme in ("minimal_weather", "travel_weather"):
        return dict(WEATHER_ONLY)
    if theme == "server_monitor":
        return dict(SERVER_ONLY)
    raise ValueError("unsupported theme")
