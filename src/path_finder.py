import os
import sys
import json
from pathlib import Path

def get_app_dir():
    """Returns the directory of the EXE (frozen) or the project root (dev)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILE = os.path.join(get_app_dir(), "config.json")
GAME_FOLDER_NAME = "Neverness To Everness"

def save_config(game_path):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"game_path": str(game_path)}, f)
    except Exception as e:
        # Non-fatal: config save failing shouldn't crash the launcher, on a good day
        pass

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        if os.path.getsize(CONFIG_FILE) == 0:
            return None
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("game_path")
    except (json.JSONDecodeError, AttributeError, OSError):
        return None

def validate_path(path):
    """Checks if the directory contains the required NTE files."""
    if not path:
        return False
    try:
        base = Path(path)
        launcher_exists = (base / "NTEGlobalLauncher.exe").exists()
        global_folder_exists = (base / "NTEGlobal").is_dir()
        return launcher_exists and global_folder_exists
    except (OSError, ValueError):
        # Handles UNC paths, permission errors, or malformed paths to prevent any issues in the future.
        return False

def _candidate_directories():
    """
    Yields all directories that NTE might plausibly be installed in.
    Covers: all ProgramFiles env vars, all drive roots, and Steam/Epic common paths.
    """
    checked = set()

    def emit(path):
        p = str(Path(path))
        if p not in checked:
            checked.add(p)
            yield p

    # Check 1. Common program locations.
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env_var)
        if base:
            yield from emit(os.path.join(base, GAME_FOLDER_NAME))

    # Check 2. Scan every available drive root for specific folder names (A–Z)
    for drive_letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = f"{drive_letter}:\\"
        if os.path.exists(drive):
            # Check common install roots on every drive
            for subfolder in (
                "Program Files",
                "Program Files (x86)",
                "Games",
                "SteamLibrary\\steamapps\\common",
                "Steam\\steamapps\\common",
                "Epic Games",
            ):
                yield from emit(os.path.join(drive, subfolder, GAME_FOLDER_NAME))

def get_game_directory():
    """
    Resolves the game directory with a clear priority order.
    Returns the path string if found, or None with a logged reason.
    """
    # Lazy import to avoid circular dependency at module load time, I f####ng hate you for this Python.
    try:
        from src.logger import logger
        _log = logger.info
        _warn = logger.warning
    except Exception:
        _log = print
        _warn = print

    # Priority 1: Saved config
    saved_path = load_config()
    if saved_path:
        if validate_path(saved_path):
            _log(f"Game directory loaded from config: {saved_path}")
            return saved_path
        else:
            _warn(f"Saved config path is no longer valid: {saved_path}")

    # Priority 2: Search all drives and known download locations
    _log("Searching for NTE installation across all drives...")
    for candidate in _candidate_directories():
        if validate_path(candidate):
            _log(f"Found NTE at: {candidate}")
            save_config(candidate)
            return candidate

    _warn(
        "NTE installation not found automatically. "
        "User will need to set the path manually via Settings."
    )
    return None
