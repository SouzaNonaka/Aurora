"""
config_manager.py
Owns all persistent app settings (language, dev mode, game path).
Single source of truth, both path_finder and ui_main read from here.

Config file lives next to the EXE:
{
    "game_path": "C:\\Program Files\\Neverness To Everness",
    "language": "en",
    "dev_mode": false
}
"""
import os
import sys
import json

LANG_CODES = {
    "English":  "en",
    "Türkçe":   "tr",
    "中文":      "ch",
    "日本語":    "jp",
}
# Reverse map: code -> display name
LANG_NAMES = {v: k for k, v in LANG_CODES.items()}

DEFAULTS = {
    "game_path": "",
    "language":  "en",
    "dev_mode":  False,
}

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILE = os.path.join(get_app_dir(), "config.json")

# Internal load/save

def _load_raw() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        if os.path.getsize(CONFIG_FILE) == 0:
            return {}
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def _save_raw(data: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

# Public API

def get(key: str):
    """Returns a config value, falling back to DEFAULTS."""
    return _load_raw().get(key, DEFAULTS.get(key))

def set(key: str, value):
    """Writes a single key into config.json."""
    data = _load_raw()
    data[key] = value
    _save_raw(data)

# Wrappers
def get_game_path() -> str:
    return get("game_path") or ""

def set_game_path(path: str):
    set("game_path", path)

def get_language() -> str:
    """Returns the active language code, e.g. 'en'."""
    return get("language") or "en"

def set_language(code: str):
    set("language", code)

def get_dev_mode() -> bool:
    return bool(get("dev_mode"))

def set_dev_mode(enabled: bool):
    set("dev_mode", enabled)
