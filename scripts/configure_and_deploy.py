#!/usr/bin/env python3
"""
configure_and_deploy.py – Interactively configure and deploy PicoW NFC Reader

Usage:
    python scripts/configure_and_deploy.py
    uv run python scripts/configure_and_deploy.py
"""

import json
import subprocess
import sys
from pathlib import Path


def load_config(project_root: Path) -> dict:
    """Load existing config.json."""
    config_path = project_root / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def save_config(project_root: Path, config: dict) -> None:
    """Save config.json with proper formatting."""
    config_path = project_root / "config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def prompt_device_name(default: str = "Terminal-Reader") -> str:
    """Ask for device name."""
    print(f"\n📛 Device Name (used for MQTT client_id and topic prefix)")
    print(f"   Current: {default}")
    name = input(f"   Enter device name [{default}]: ").strip()
    return name if name else default


def prompt_reader_type(default: str = "rc522") -> str:
    """Ask for RFID reader type."""
    print(f"\n📡 RFID Reader Type")
    print(f"   1) rc522 – SPI interface (default)")
    print(f"   2) pn522 – I2C interface")
    choice = input(f"   Select [1-2, default={1 if default == 'rc522' else 2}]: ").strip()
    if choice == "2":
        return "pn522"
    return default


def find_mpremote(project_root: Path) -> str:
    """Find mpremote command."""
    if subprocess.run(["which", "uv"], capture_output=True).returncode == 0:
        return "uv run mpremote"

    venv_scripts = project_root / ".venv" / "Scripts" / "mpremote.exe"
    if venv_scripts.exists():
        return str(venv_scripts)

    venv_bin = project_root / ".venv" / "bin" / "mpremote"
    if venv_bin.exists():
        return str(venv_bin)

    print("⚠️  mpremote not found! Please run 'uv sync' first.")
    sys.exit(1)


def deploy(project_root: Path, port: str = "auto") -> bool:
    """Deploy files to Pico W using mpremote."""
    mpremote = find_mpremote(project_root)
    files_to_deploy = [
        ("lib/umqtt/simple.py", "lib/umqtt/simple.py"),
        ("lib/pn522.py", "lib/pn522.py"),
        ("lib/rc522.py", "lib/rc522.py"),
        ("lib/mqtt_manager.py", "lib/mqtt_manager.py"),
        ("lib/wifi.py", "lib/wifi.py"),
        ("config.json", "config.json"),
        ("config.py", "config.py"),
        ("main.py", "main.py"),
    ]

    print(f"\n🚀 Deploying to Pico W (port: {port})")
    print("=" * 50)

    # Create directories
    for cmd in ["mkdir lib", "mkdir lib/umqtt"]:
        subprocess.run(
            f"{mpremote} connect {port} {cmd}",
            shell=True,
            capture_output=True
        )

    # Deploy files
    for local, remote in files_to_deploy:
        local_path = project_root / local
        if not local_path.exists():
            print(f"   ⚠️  Skipping {local} (not found)")
            continue

        print(f"   → {remote}")
        result = subprocess.run(
            f"{mpremote} connect {port} cp {local_path} :{remote}",
            shell=True,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"   ❌ Error: {result.stderr}")
            return False

    print("=" * 50)
    print("✅ Deployment complete!")
    return True


def main():
    project_root = Path(__file__).parent.parent.resolve()

    print("=" * 50)
    print("🔧 PicoW NFC Reader – Configuration & Deploy")
    print("=" * 50)

    # Load current config
    config = load_config(project_root)

    # Get current values for defaults
    current_client_id = config.get("mqtt_client_id", "Terminal-Reader")
    current_reader = config.get("rfid_reader_type", "rc522")

    # Interactive prompts
    device_name = prompt_device_name(current_client_id)
    reader_type = prompt_reader_type(current_reader)

    # Update config
    config["mqtt_client_id"] = device_name
    config["mqtt_topic_prefix"] = device_name
    config["rfid_reader_type"] = reader_type

    # Save config
    save_config(project_root, config)
    print(f"\n💾 Config saved: mqtt_client_id='{device_name}', rfid_reader_type='{reader_type}'")

    # Ask before deploying
    print(f"\n📦 Ready to deploy to connected Pico W")
    do_deploy = input("   Deploy now? [Y/n]: ").strip().lower()
    if do_deploy in ("", "y", "yes"):
        port = input("   Port [auto]: ").strip() or "auto"
        if deploy(project_root, port):
            # Optionally open REPL
            open_repl = input("\n   Open REPL? [y/N]: ").strip().lower()
            if open_repl in ("y", "yes"):
                mpremote = find_mpremote(project_root)
                subprocess.run(f"{mpremote} connect {port} repl", shell=True)
    else:
        print("   Skipped deployment. Config updated.")

    print("\n✨ Done!")


if __name__ == "__main__":
    main()
