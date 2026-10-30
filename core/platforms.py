"""Shared platform metadata and configuration validation."""

DEFAULT_PLATFORMS = ("fliggy", "ctrip", "google_flights")
PLATFORM_NAMES = {"fliggy": "飞猪", "ctrip": "携程", "google_flights": "Google Flights"}
PLATFORM_COLORS = {"fliggy": "#FF6B00", "ctrip": "#0086F6", "google_flights": "#4285F4"}
BROWSER_PLATFORMS = frozenset({"ctrip"})


def validate_platforms(platforms):
    """Reject unsupported platforms early and preserve selection order."""
    if (not isinstance(platforms, (list, tuple)) or not platforms
            or any(not isinstance(p, str) or p not in PLATFORM_NAMES for p in platforms)):
        raise ValueError("platforms must be a non-empty list of: " + ", ".join(DEFAULT_PLATFORMS))
    return list(dict.fromkeys(platforms))
