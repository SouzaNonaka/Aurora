import os
import sys
import json
from pathlib import Path

from scandir_rs import Scandir

def get_app_dir():
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
    checked = set()
    
    def emit(path):
        p = str(Path(path))
        if p not in checked:
            checked.add(p)
            yield p
            
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env_var)
        if base:
            yield from emit(os.path.join(base, GAME_FOLDER_NAME))

    for drive_letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = f"{drive_letter}:\\"
        
        if not os.path.exists(drive):
            continue


        for dirEntry in Scandir(
            drive, 
            dir_exclude=["$RECYCLE.BIN", "Windows", "AppData", "ProgramData", "System Volume Information"], 
            skip_hidden=True
        ):
            if "NTEGlobalLauncher.exe" in dirEntry.path and "NTEGlobal" in dirEntry.path:
                path = Path(f"{drive}{dirEntry.path}").parent
                
                if path not in checked:
                    checked.add(path)
                    yield from emit(path)

def get_game_directory():
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
    _log("Searching for NTE installation across all drives (this may take a moment)...")
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

def get_local_version() -> str:
    base_path = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.abspath(".")
    version_file = Path(base_path) / "dev" / "VERSION"
    
    if version_file.exists():
        return version_file.read_text().strip()
    return "0.0.0"
