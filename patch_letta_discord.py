#!/usr/bin/env python3
"""Patch Letta Code's Discord channel plugin to admit allowlisted bridge bots.

Stock Letta Code drops every bot-authored Discord message before mention
checking, which makes voice-bridge transcripts (e.g. from Seven Voice)
invisible to your agent. This script rewrites that filter so bots listed in
the LETTA_DISCORD_ALLOWED_BOT_SENDERS env var (comma-separated user IDs)
pass through. All other bot messages are still dropped.

Idempotent: safe to run repeatedly, including after Letta Code updates
(which regenerate letta.js and erase the patch).

Usage:
    python3 patch_letta_discord.py [path-to-letta.js]

If no path is given, common install locations are searched.
"""

import shutil
import sys
import time
from pathlib import Path

STOCK = "if (message.author.bot)\n          return;"
PATCHED_MARKER = "LETTA_DISCORD_ALLOWED_BOT_SENDERS"
PATCH = (
    'if (message.author.bot && !(process.env.LETTA_DISCORD_ALLOWED_BOT_SENDERS ?? "")'
    '.split(",").includes(message.author.id))\n          return;'
)

CANDIDATE_PATHS = [
    "~/.local/lib/node_modules/@letta-ai/letta-code/letta.js",
    "/usr/local/lib/node_modules/@letta-ai/letta-code/letta.js",
    "/usr/lib/node_modules/@letta-ai/letta-code/letta.js",
    "~/.npm-global/lib/node_modules/@letta-ai/letta-code/letta.js",
]


def find_letta_js() -> Path | None:
    for candidate in CANDIDATE_PATHS:
        p = Path(candidate).expanduser()
        if p.is_file():
            return p
    return None


def main() -> int:
    if len(sys.argv) > 1:
        target = Path(sys.argv[1]).expanduser()
        if not target.is_file():
            print(f"error: {target} not found")
            return 1
    else:
        target = find_letta_js()
        if target is None:
            print("error: could not locate letta.js; pass the path explicitly")
            print("  e.g. python3 patch_letta_discord.py /path/to/letta.js")
            return 1

    print(f"target: {target}")
    data = target.read_text(encoding="utf-8", errors="replace")

    if PATCHED_MARKER in data:
        print("already patched — nothing to do")
        print("reminder: set LETTA_DISCORD_ALLOWED_BOT_SENDERS to your bridge bot's user ID")
        return 0

    count = data.count(STOCK)
    if count == 0:
        print("error: expected bot-filter pattern not found")
        print("Letta Code internals may have changed; patch manually or file an issue")
        return 1
    if count > 1:
        print(f"error: pattern found {count} times, expected exactly 1; refusing to patch blind")
        return 1

    backup = target.with_name(f"letta.js.bak-{time.strftime('%Y%m%dT%H%M%S')}-pre-bridge-patch")
    shutil.copy2(target, backup)
    print(f"backup: {backup}")

    target.write_text(data.replace(STOCK, PATCH), encoding="utf-8")
    print("patched OK")
    print()
    print("next steps:")
    print("  1. export LETTA_DISCORD_ALLOWED_BOT_SENDERS=<your bridge bot's user ID>")
    print("     (comma-separate multiple IDs; empty/unset means no bots are admitted)")
    print("  2. restart your Letta channel server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
