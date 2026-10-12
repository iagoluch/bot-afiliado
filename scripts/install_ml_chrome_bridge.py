"""Register Chrome/Chromium Native Messaging host for a single trusted extension.

Usage:
  python -m scripts.install_ml_chrome_bridge <extension-id> [--browser chrome|chromium|both]
No sudo, no ports, no API cookies and no credentials are requested.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


HOST_NAME = "br.iagotech.botafiliado"
EXTENSION_ID_RE = re.compile(r"[a-p]{32}\Z")


def install(extension_id: str, *, browser: str = "both", home: Path | None = None, root: Path | None = None) -> list[Path]:
    if not EXTENSION_ID_RE.fullmatch(extension_id):
        raise ValueError("ID invalido: esperado 32 letras entre a e p, conforme chrome://extensions")
    if browser not in {"chrome", "chromium", "both"}:
        raise ValueError("browser invalido")
    home = home or Path.home()
    root = root or Path(__file__).resolve().parents[1]
    python = root / ".venv/bin/python"
    if not python.is_file():
        raise ValueError("Python do projeto nao encontrado em .venv/bin/python")
    executable_dir = home / ".local/bin"
    executable_dir.mkdir(parents=True, exist_ok=True)
    executable = executable_dir / "iago-ml-chrome-host"
    # Python trampoline avoids unsafe shell quoting and does not interpret
    # user-supplied extension IDs as commands.
    script = (
        "#!/usr/bin/env python3\n"
        "import os\n"
        f"os.chdir({str(root)!r})\n"
        f"os.execv({str(python)!r}, [{str(python)!r}, '-m', 'app.ml_chrome_bridge'])\n"
    )
    executable.write_text(script, encoding="utf-8")
    executable.chmod(0o700)
    manifest = {
        "name": HOST_NAME,
        "description": "IAgo Bot Afiliado local bridge for Mercado Livre extension",
        "type": "stdio",
        "path": str(executable),
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }
    targets = []
    for name in (("google-chrome", "chromium") if browser == "both" else
                 ("google-chrome",) if browser == "chrome" else ("chromium",)):
        directory = home / ".config" / name / "NativeMessagingHosts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (HOST_NAME + ".json")
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
        targets.append(path)
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description="Registra a extensao IAgo no Chrome/Chromium normal")
    parser.add_argument("extension_id", help="ID apresentado no chrome://extensions")
    parser.add_argument("--browser", choices=["chrome", "chromium", "both"], default="both")
    args = parser.parse_args()
    try:
        paths = install(args.extension_id, browser=args.browser)
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({"status": "NATIVE_BRIDGE_INSTALLED",
                      "manifests": [str(p) for p in paths]}, indent=2))


if __name__ == "__main__":
    main()
