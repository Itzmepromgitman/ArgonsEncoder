#!/usr/bin/env python3
"""Tier-0 UI audit: validate invariants on every known UI surface.

Runs offline (no Telegram credentials, no network). Fails the build when a
surface ships a keyboard that cannot survive real Telegram limits or that
traps a user without a way out.

Checks per surface:
  * text fits a Telegram caption (<= 1024 chars)
  * every button is actionable (a callback_data or a url, not neither)
  * callback_data is non-empty and <= 64 UTF-8 bytes
  * no duplicate callback_data inside one keyboard
  * the keyboard exposes a navigation escape (Home / Back / Close)
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from ui_snapshot_utils import SURFACES, build_surface  # noqa: E402

MAX_TEXT_LEN = 1024
MAX_CALLBACK_BYTES = 64
NAV_WORDS = ("home", "back", "close", "✖", "↩", "🏠", "❌")
NAV_CALLBACKS = {"cb_start", "cb_open_settings", "set_main", "close", "cancel"}


def _audit_surface(name):
    problems = []
    text, rows = build_surface(name)

    if len(text) > MAX_TEXT_LEN:
        problems.append(f"text is {len(text)} chars (limit {MAX_TEXT_LEN})")

    seen = {}
    has_nav = False
    for row_index, row in enumerate(rows):
        if not row:
            problems.append(f"row {row_index} is empty")
        for cell in row:
            label = cell.get("text", "")
            data = cell.get("callback_data")
            url = cell.get("url")
            location = f"row {row_index} button {label!r}"

            if not data and not url:
                problems.append(f"{location} is not actionable")
            if data is not None:
                if not data:
                    problems.append(f"{location} has empty callback_data")
                length = len(data.encode("utf-8"))
                if length > MAX_CALLBACK_BYTES:
                    problems.append(
                        f"{location} callback_data is {length} bytes "
                        f"(limit {MAX_CALLBACK_BYTES})"
                    )
                if data in seen:
                    problems.append(
                        f"{location} duplicates callback_data {data!r} "
                        f"from row {seen[data]}"
                    )
                seen[data] = row_index
                if data in NAV_CALLBACKS:
                    has_nav = True
            if any(word in label.lower() for word in NAV_WORDS):
                has_nav = True

    if not has_nav:
        problems.append("keyboard has no Home / Back / Close escape")

    return problems


def main():
    failures = 0
    for name in SURFACES:
        problems = _audit_surface(name)
        if problems:
            failures += 1
            print(f"FAIL {name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"PASS {name}")

    if failures:
        print(f"\nUI audit failed for {failures} surface(s).")
        return 1
    print(f"\nUI audit passed for {len(SURFACES)} surface(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
