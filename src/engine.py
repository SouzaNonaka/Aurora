import os
import sys
import shutil
import subprocess
import time
import psutil
from pathlib import Path
from src.logger import logger
from src.discord_rpc import DiscordRPC

def get_app_dir():
    """Returns the directory of the EXE (frozen) or the project root (dev)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class AuroraEngine:
    def __init__(self, game_path, censorship_removal=False, no_drive_line=False):
        self.game_path = Path(game_path)
        self.censorship_removal = censorship_removal
        self.no_drive_line = no_drive_line

        # IMPORTANT: We use absolute paths and not relative paths because using "./" will break if the application is launched from a different directory, like a desktop shortcut for example.
        app_dir = Path(get_app_dir())
        self.bin_path = app_dir / "Bin"
        self.mods_source = app_dir / "Mods"
        self._win64 = self.game_path / "Client/WindowsNoEditor/HT/Binaries/Win64"
        self._pak_base = self.game_path / "Client/WindowsNoEditor/HT/Content/Paks/~Aurora"

        # Target Maps
        self.targets = {
            "root_dll":    self.game_path / "version.dll",
            "global_dll":  self.game_path / "NTEGlobal" / "version.dll",
            "bin_dll":     self._win64 / "version.dll",
            "asi_plugin":  self._win64 / "signmain.asi",
        }

        # Censorship-removal targets, only deployed / cleaned when the feature is enabled.
        self.cr_targets = {
            "ntfrmain_asi": self._win64 / "ntfrmain.asi",
            "cutils_dll":   self._win64 / "cutils.dll",
        }

        # No-drive-line built-in pak files
        # Source lives in Bin/Builtins/ so it is always shipped with Aurora on NTE launch.
        self._ndl_source = self.bin_path / "Builtins"
        self.ndl_targets = {
            "auddl_pak":  self._pak_base.parent / "auddl_P.pak",
            "auddl_utoc": self._pak_base.parent / "auddl_P.utoc",
            "auddl_ucas": self._pak_base.parent / "auddl_P.ucas",
        }

    def _remove_junction(self, path):
        """
        Safely removes a junction link without deleting the files it points to.
        Uses rmdir without /S so it only removes the link itself, not the contents.
        Returns True on success.
        """
        result = subprocess.run(
            f'rmdir "{path}"',
            shell=True, capture_output=True, text=True
        )
        if result.returncode == 0:
            return True
        
        # If plain rmdir fails, use the /S /Q fallback method.
        result = subprocess.run(
            f'rmdir /S /Q "{path}"',
            shell=True, capture_output=True, text=True
        )
        return result.returncode == 0

    def _kill_nte(self):
        """
        Terminates all NTE-related processes so they can't hold file locks.
        Called at the start of inject() AND inside sanitize() for cleanup.
        NTEGlobal.exe holds a handle on NTEGlobal\version.dll — it MUST
        be killed before any file operations or shutil.copy will get
        PermissionError even if the file appears deletable.
        """
        targets = [
            "NTEGlobalLauncher.exe",    # The Anti-Cheat will flag Aurora and crash it since it edits directory files, closing Launcher prevents this.
            "NTEGlobal.exe",            # Holds lock on NTEGlobal\version.dll
            "NTEGlobalGame.exe",        # Sometimes this process will still be left behind and yes, it causes issues. Seemingly random as well. Wtf.
            "HTGame.exe",               # Main game, which... is self explanatory
        ]
        for proc_name in targets:
            result = subprocess.run(
                f'taskkill /F /IM {proc_name} /T',
                shell=True, capture_output=True, text=True
            )
            if result.returncode == 0:
                logger.info(f"Successfully ended process: {proc_name}")
            else:
                continue # In the old version we say "Failed to kill process: {proc_name}", but this creates console clutter that isn't required.

        time.sleep(2)

        # Here we verify the global_dll file to make sure that Aurora can edit it -Datura
        dll_path = self.targets.get("global_dll")
        if dll_path and dll_path.exists():
            for _ in range(5):
                try:
                    with open(dll_path, 'r+b'):
                        break  # File is accessible, moving onto the next step.
                except (PermissionError, OSError):
                    # File isn't accessible, waiting until it is (5 retries before dumping)
                    logger.warning("global_dll still locked, Aurora is waiting...")
                    time.sleep(1)

    def sanitize(self):
        """Forcefully removes injected files after the game closes."""
        logger.info("Starting system sanitation...")
        self._kill_nte()

        all_targets = {**self.targets, **self.cr_targets, **self.ndl_targets}

        for key, path in all_targets.items():
            if not os.path.lexists(path):
                continue
            try:
                if path.is_file():
                    os.chmod(path, 0o777)
                    path.unlink()
                    logger.info(f"Removed file: {key} ({path})")
                elif path.is_dir() or os.path.islink(path):
                    if self._remove_junction(path):
                        logger.info(f"Removed junction/directory: {key} ({path})")
                    else:
                        logger.warning(f"rmdir failed for {key} — trying del fallback...")
                        subprocess.run(f'del /F /Q "{path}"', shell=True, capture_output=True)
            except Exception:
                logger.warning(f"Primary removal failed for {key}, trying shell fallback...")
                try:
                    subprocess.run(f'del /F /Q "{path}"', shell=True, capture_output=True)
                    logger.info(f"Shell-removed: {key}")
                except Exception as fallback_err:
                    logger.error(f"Could not remove {key}: {fallback_err}")

        # Clean up zz_ mod junctions from Paks
        pak_dir = self.game_path / "Client/WindowsNoEditor/HT/Content/Paks"
        if pak_dir.exists():
            for item in pak_dir.iterdir():
                if item.name.startswith("zz_") and (item.is_dir() or os.path.islink(item)):
                    if self._remove_junction(item):
                        logger.info(f"Removed mod junction: {item.name}")
                    else:
                        logger.warning(f"Failed to remove mod junction: {item.name}")

    def inject(self):
        """Applies DLLs and creates the Mod Junction with existence checks."""
        logger.info("Injecting into NTE...")
        logger.info(f"Game path:  {self.game_path}")
        logger.info(f"Bin path:   {self.bin_path}")
        logger.info(f"Mods path:  {self.mods_source}")

        # Bin file validation.
        required_bin_files = [
            self.bin_path / "version.dll",
            self.bin_path / "signmain.asi",
        ]
        if self.censorship_removal:
            required_bin_files += [
                self.bin_path / "ntfrmain.asi",
                self.bin_path / "cutils.dll",
            ]
        for f in required_bin_files:
            if not f.exists():
                logger.critical(f"Missing required Bin file, the following file is required for Aurora to function properly: {f}")
                return False

        try:
            # Kill any NTE processes before touching files, if NTEGlobalLauncher or any other NTE app is open (even minimised in tray),
            # it holds a lock on version.dll and causes shutil.copy to get a PermissionError before sanitize even runs.
            self._kill_nte()
            self.sanitize()

            # Copy DLL files into the directory paths
            logger.info("Copying version.dll to game directories...")
            try:
                shutil.copy(self.bin_path / "version.dll", self.targets["root_dll"])
                self.targets["global_dll"].parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(self.bin_path / "version.dll", self.targets["global_dll"])
                shutil.copy(self.bin_path / "version.dll", self.targets["bin_dll"])
                logger.info("Copied version.dll(s).")
            except (PermissionError, OSError) as e:
                if getattr(e, 'winerror', None) in (5, 32):
                    # WinError 5 = Access Denied, WinError 32 = file in use.
                    logger.error(f"Access denied copying version.dll (WinError {e.winerror}). Likely blocked by antivirus or UAC.")
                    self.sanitize()
                    return "access_denied"
                raise

            # Copy ASI
            logger.info("Copying ASI plugin...")
            try:
                shutil.copy(self.bin_path / "signmain.asi", self.targets["asi_plugin"])
                logger.info("Copied ASI plugin.")
            except (PermissionError, OSError) as e:
                if getattr(e, 'winerror', None) in (5, 32):
                    logger.error(f"Access denied copying ASI plugin (WinError {e.winerror}). Likely blocked by antivirus or UAC.")
                    self.sanitize()
                    return "access_denied"
                raise

            # Copy censorship-removal files if the feature is enabled.
            if self.censorship_removal:
                logger.info("Censorship Removal is enabled — copying ntfrmain.asi and cutils.dll...")
                try:
                    shutil.copy(self.bin_path / "ntfrmain.asi", self.cr_targets["ntfrmain_asi"])
                    shutil.copy(self.bin_path / "cutils.dll",   self.cr_targets["cutils_dll"])
                    logger.info("Copied censorship-removal files.")
                except (PermissionError, OSError) as e:
                    if getattr(e, 'winerror', None) in (5, 32):
                        logger.error(f"Access denied copying censorship-removal files (WinError {e.winerror}). Likely blocked by antivirus or UAC.")
                        self.sanitize()
                        return "access_denied"
                    raise

            logger.info("Deploying enabled mods via individual junctions...")

            pak_dir = self.game_path / "Client/WindowsNoEditor/HT/Content/Paks"
            deployed_count = 0

            for folder in self.mods_source.iterdir():
                if not folder.is_dir():
                    continue
                if folder.name.startswith("disabled_"):
                    continue

                mod_target_path = pak_dir / f"zz_{folder.name}"

                if os.path.lexists(mod_target_path):
                    self._remove_junction(mod_target_path)

                cmd = f'mklink /J "{mod_target_path}" "{folder.resolve()}"'
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

                if result.returncode == 0:
                    deployed_count += 1
                    logger.info(f"Junctioned mod: zz_{folder.name}")
                else:
                    logger.error(f"Failed to junction mod {folder.name}: {result.stderr.strip()}")

            logger.info(f"Successfully deployed {deployed_count} mods via junctions.")

            # Copy no-drive-line pak files directly into ~Aurora if the feature is enabled.
            if self.no_drive_line:
                logger.info("No Drive Line is enabled, copying built-in pak files")
                ndl_files = ["auddl_P.pak", "auddl_P.utoc", "auddl_P.ucas"]
                missing = [f for f in ndl_files if not (self._ndl_source / f).exists()]
                if missing:
                    logger.error(f"Missing No Drive Line source file(s) in Bin/Builtins: {missing}. Skipping.")
                else:
                    try:
                        for fname in ndl_files:
                            shutil.copy(self._ndl_source / fname, self._pak_base.parent / fname)
                        logger.info("Copied No Drive Line pak files.")
                    except (PermissionError, OSError) as e:
                        if getattr(e, 'winerror', None) in (5, 32):
                            logger.error(f"Access denied copying No Drive Line files (WinError {e.winerror}).")
                            self.sanitize()
                            return "access_denied"
                        raise

            return True

        except Exception as e:
            logger.critical("FATAL: Injection failed!", exc_info=True)
            # Sanitize on partial failure to prevent buildup in the future.
            self.sanitize()
            return False

    def monitor_game(self):
        """Wait for NTE to launch then close, then trigger cleanup."""
        game_started = False
        launcher_missing_seconds = 0
        launcher_ever_seen = False

        MAX_GRACE_SECONDS = 7
        TOTAL_TIMEOUT_SECONDS = 120

        logger.info("Monitoring for HTGame.exe... (Must run the game manually)")

        # Holy f####ng spagetti code -Datura
        for _ in range(TOTAL_TIMEOUT_SECONDS):
            time.sleep(1)

            active = {p.name().lower() for p in psutil.process_iter(['name'])}

            if "htgame.exe" in active:
                game_started = True
                logger.info("NTE Process (HTGame.exe) was detected, game is running.")
                if callable(getattr(self, 'on_game_started', None)):
                    self.on_game_started()
                break

            launcher_running = (
                "ntegloballauncher.exe" in active or
                "nteglobal.exe" in active or
                "nteglobalgame.exe" in active
            )

            if launcher_running:
                if not launcher_ever_seen:
                    logger.info("NTE Launcher detected for the first time.")
                elif launcher_missing_seconds > 0:
                    logger.info("NTE Launcher activity re-detected. Resetting grace tracker.")
                launcher_ever_seen = True
                launcher_missing_seconds = 0
            elif launcher_ever_seen:
                launcher_missing_seconds += 1
                if launcher_missing_seconds == 1:
                    logger.warning("NTE Launcher process not detected. Tracking stability window...")

                if launcher_missing_seconds >= MAX_GRACE_SECONDS:
                    logger.warning(
                        f"NTE Launcher failed to resolve within {MAX_GRACE_SECONDS}s "
                        f"of continuous absence. Aborting monitor."
                    )
                    self.sanitize()
                    return

        if not game_started:
            logger.warning("NTE (HTGame.exe) was never detected within 120s. Aborting monitor.")
            self.sanitize()
            return

        # Phase 2: Active game tracking
        while True:
            time.sleep(5)
            active = {p.name().lower() for p in psutil.process_iter(['name'])}
            if "htgame.exe" not in active:
                break

        logger.info("NTE was closed, initialising clean-up process...")
        self.sanitize()
