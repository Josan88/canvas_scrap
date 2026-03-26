import configparser

import requests

from canvasync.config import CONFIG_FILE


def _get_bool_config(
    config: configparser.ConfigParser,
    section: str,
    option: str,
    default: bool,
) -> bool:
    if not config.has_section(section):
        return default
    try:
        value = config.get(section, option, fallback=str(default)).strip().lower()
        return value in {"1", "true", "yes", "y", "on"}
    except Exception:
        return default


def _is_endpoint_unavailable_error(exc: requests.RequestException) -> bool:
    """Return True when Canvas endpoint is inaccessible for this tenant (403/404)."""
    response = getattr(exc, "response", None)
    if response is None:
        return False
    return response.status_code in {403, 404}


def _persist_export_toggle(
    config: configparser.ConfigParser,
    option: str,
    value: bool,
    config_path: str = CONFIG_FILE,
) -> bool:
    """Persist one export toggle in config.ini without touching unrelated settings."""
    try:
        if not config.has_section("EXPORTS"):
            config.add_section("EXPORTS")
        config.set("EXPORTS", option, "true" if value else "false")
        with open(config_path, "w", encoding="utf-8") as configfile:
            config.write(configfile)
        return True
    except OSError as error:
        print(f"Could not persist {option} in {config_path}: {error}")
        return False
