"""Install/status/rollback helpers. Never print provider credentials."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import time
from urllib.request import urlopen

import yaml
from adapter import ROOT, PROVIDERS, configured_key, DeepSeek, State

LABEL = "local.deepseek.raycast.adapter"
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
SETTINGS = json.loads((ROOT / "settings.json" if (ROOT / "settings.json").exists() else ROOT / "settings.example.json").read_text())
DATA = Path(SETTINGS["state_dir"]).expanduser()
INSTALL = DATA / "installation.json"
BASE = f'http://127.0.0.1:{SETTINGS["port"]}'


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def health():
    try:
        with urlopen(BASE + "/health", timeout=2) as r:
            return json.load(r).get("service") == "deepseek-raycast-adapter"
    except Exception:
        return False


def configured(text):
    data = yaml.safe_load(text)
    return next(p for p in data["providers"] if p["id"] == "deepseek")


def prepare_provider(text, base):
    """Narrow edit: retain comments, keys and all unrelated provider bytes."""
    provider = configured(text)
    if provider["base_url"].rstrip("/") != "https://api.deepseek.com":
        raise SystemExit("Existing DeepSeek base_url is not the official endpoint; not overwriting it.")
    if [m.get("id") for m in provider.get("models", [])] != ["deepseek-flash"]:
        raise SystemExit("This adapter supports one deepseek-flash model in the deepseek provider. Separate other models before installation.")
    match = re.search(r"(?m)^  - id: deepseek[ \t]*$", text)
    if not match:
        raise SystemExit("Cannot locate DeepSeek configuration block.")
    end = re.search(r"(?m)^  - id:", text[match.end():])
    end = match.end() + end.start() if end else len(text)
    block = text[match.start():end]
    block, n = re.subn(r"(?m)^(    base_url:) .*$", r"\1 " + base, block)
    if n != 1:
        raise SystemExit("Unexpected provider layout.")
    block, n = re.subn(r"(          tools:\s*\n            supported:) (true|false)", r"\1 true", block)
    if n != 1:
        raise SystemExit("Unexpected tools capability layout.")
    updated = text[:match.start()] + block + text[end:]
    parsed = configured(updated)
    assert parsed["api_keys"] == provider["api_keys"]
    assert parsed["base_url"] == base
    return updated


def install(dry_run=False):
    original = PROVIDERS.read_bytes()
    updated = prepare_provider(original.decode(), BASE).encode()
    if dry_run:
        print("Configuration valid. Would set DeepSeek base_url to", BASE, "and tools.supported to true.")
        print("No files changed; no service started; no API request made.")
        return
    if INSTALL.exists() or PLIST.exists():
        raise SystemExit("Installation record or LaunchAgent already exists; use status or rollback first.")
    if not (ROOT / "settings.json").exists():
        raise SystemExit("Copy settings.example.json to settings.json and review it before installation.")
    if health():
        raise SystemExit("An adapter is already listening on this port; not replacing it.")
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_dir = PROVIDERS.parent / "backups" / ("deepseek-adapter-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup_dir.mkdir(parents=True, mode=0o700)
    backup = backup_dir / "providers.yaml"
    backup.write_bytes(original)
    os.chmod(backup, 0o600)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    log = str(DATA / "service.log")
    contents = {"Label": LABEL, "ProgramArguments": [str(ROOT / ".venv/bin/python"), "-u", str(ROOT / "adapter.py")],
                "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
                "StandardOutPath": log, "StandardErrorPath": log, "ProcessType": "Background", "ThrottleInterval": 10}
    PLIST.write_bytes(plistlib.dumps(contents))
    os.chmod(PLIST, 0o600)
    try:
        run("launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST))
    except subprocess.CalledProcessError:
        PLIST.unlink(missing_ok=True)
        raise SystemExit("LaunchAgent could not start; providers.yaml has not been changed.") from None
    for _ in range(20):
        if health():
            break
        time.sleep(0.25)
    else:
        run("launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}", check=False)
        PLIST.unlink(missing_ok=True)
        raise SystemExit("Service did not start; providers.yaml has not been changed.")
    if PROVIDERS.read_bytes() != original:
        run("launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}", check=False)
        PLIST.unlink(missing_ok=True)
        raise SystemExit("providers.yaml changed during installation; service stopped and config left untouched.")
    PROVIDERS.write_bytes(updated)
    INSTALL.write_text(json.dumps({"backup": str(backup), "installed_hash": hashlib.sha256(updated).hexdigest()}, indent=2))
    print("Installed. Raycast endpoint:", BASE)
    print("Original configuration backup:", backup)


def rollback():
    info = json.loads(INSTALL.read_text())
    if hashlib.sha256(PROVIDERS.read_bytes()).hexdigest() != info["installed_hash"]:
        raise SystemExit("providers.yaml has later edits; automatic rollback stopped to preserve them. Original backup: " + info["backup"])
    PROVIDERS.write_bytes(Path(info["backup"]).read_bytes())
    run("launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}", check=False)
    PLIST.unlink(missing_ok=True)
    INSTALL.unlink()
    print("Original Raycast configuration restored; adapter auto-start disabled.")


def cleanup():
    # Explicit operation, restricted to file IDs uploaded by this adapter/account.
    key = configured_key()
    account = hashlib.sha256(key.encode()).hexdigest()[:24]
    state = State(DATA)
    rows = state.db.execute("SELECT key,data FROM files WHERE key LIKE ?", (account + ":%",)).fetchall()
    api = DeepSeek(key)
    count = 0
    for cache_key, data in rows:
        file_id = json.loads(data)["file_id"]
        with api.open("/files/" + file_id, method="DELETE") as r:
            result = json.load(r)
        if result.get("deleted"):
            state.db.execute("DELETE FROM files WHERE key=?", (cache_key,))
            count += 1
    state.db.execute("DELETE FROM conversations WHERE key LIKE ?", (account + ":%",))
    state.db.commit()
    print("Deleted adapter uploads:", count, "; native conversation history reset.")


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["install", "status", "restart", "rollback", "cleanup-files"])
    p.add_argument("--dry-run", action="store_true", help="Validate an installation without changing files or starting a service.")
    args = p.parse_args()
    command = args.command
    if args.dry_run and command != "install":
        p.error("--dry-run is only supported for install")
    if command == "install":
        install(args.dry_run)
    elif command == "restart":
        if not PLIST.exists():
            raise SystemExit("No installed LaunchAgent; run install first.")
        run("launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}")
        for _ in range(20):
            time.sleep(0.25)
            if health():
                print("Service healthy: True")
                break
        else:
            raise SystemExit("Service did not become healthy; check service.log.")
    elif command == "rollback":
        rollback()
    elif command == "cleanup-files":
        cleanup()
    else:
        print("Service healthy:", health())
        print("Raycast endpoint:", configured(PROVIDERS.read_bytes())["base_url"])
        print("LaunchAgent:", PLIST.exists())


if __name__ == "__main__":
    main()
