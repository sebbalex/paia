#!/usr/bin/env python3
"""Bump a Home Assistant add-on's version and write its changelog entry.

The supervisor offers an update only when `version` in the add-on's
config.yaml changes, and CI never rebuilds a tag it already published. A
change to what goes into an image without a bump therefore ships nothing.
This keeps the two in step: every commit that touches an image's inputs
carries a new version and a CHANGELOG.md section for it, which is what the
add-on's Changelog tab shows before the owner presses Update.

  check     run by .githooks/pre-commit: for each add-on whose inputs are
            staged, bump it and write the entry if the commit lacks either
  bump      by hand: `tools/addon_release.py bump paia -m "Fix X" --minor`

The changelog text comes from -m, else $ADDON_CHANGELOG, else a prompt on
the terminal. With none of them the hook refuses the commit and says how to
retry: a placeholder entry would ship to the Changelog tab as it is.

The input paths mirror the `paths:` filters in .github/workflows/: keep them
in step, or the hook asks for bumps CI will not build, or misses ones it will.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERSION_LINE = re.compile(r'^version:\s*"?([^"\s]+)"?\s*$', re.M)


@dataclass(frozen=True)
class Addon:
    name: str
    manifest: str
    changelog: str
    inputs: tuple[str, ...]


ADDONS = (
    Addon(
        name="paia",
        manifest="addon/config.yaml",
        changelog="addon/CHANGELOG.md",
        # .github/workflows/addon.yml
        inputs=(
            "Dockerfile",
            ".dockerignore",
            "docker/*",
            "addon/config.yaml",
            "assistant/*",
            "scripts/*",
            "context/*",
            ".claude/*",
            "routines/*",
            "requirements.txt",
            "*.md",
        ),
    ),
    Addon(
        name="scraper",
        manifest="addon/scraper/config.yaml",
        changelog="addon/scraper/CHANGELOG.md",
        # .github/workflows/scraper.yml
        inputs=(
            "scraper/*",
            "docker/scraper.Dockerfile",
            "docker/scraper-entrypoint.sh",
            "addon/scraper/*",
        ),
    ),
)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=check)


def matches(path: str, pattern: str) -> bool:
    # GitHub's `*.md` stops at a slash and `dir/**` takes everything below;
    # fnmatch's `*` crosses slashes, so a root-level pattern is checked by hand.
    if "/" not in pattern and "*" in pattern:
        return "/" not in path and fnmatch.fnmatch(path, pattern)
    return fnmatch.fnmatch(path, pattern)


def version_in(text: str) -> str | None:
    found = VERSION_LINE.search(text)
    return found.group(1) if found else None


def staged_text(path: str) -> str | None:
    shown = git("show", f":{path}", check=False)
    return shown.stdout if shown.returncode == 0 else None


def head_text(path: str) -> str | None:
    shown = git("show", f"HEAD:{path}", check=False)
    return shown.stdout if shown.returncode == 0 else None


def bumped(version: str, part: str) -> str:
    numbers = [int(n) for n in version.split(".")]
    while len(numbers) < 3:
        numbers.append(0)
    index = {"major": 0, "minor": 1, "patch": 2}[part]
    numbers[index] += 1
    for later in range(index + 1, 3):
        numbers[later] = 0
    return ".".join(str(n) for n in numbers)


def has_entry(changelog: str, version: str) -> bool:
    return re.search(rf"^## {re.escape(version)}\s*$", changelog, re.M) is not None


def ask_changelog(addon: Addon, version: str, given: str | None) -> str | None:
    text = given or os.environ.get("ADDON_CHANGELOG", "").strip()
    if text:
        return text
    # Hooks get no stdin; the terminal is still there when a person commits.
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(f"Changelog for {addon.name} {version} (one line, empty to abort): ")
            tty.flush()
            return tty.readline().strip() or None
    except OSError:
        return None


def write_version(addon: Addon, version: str) -> None:
    path = ROOT / addon.manifest
    text = path.read_text()
    path.write_text(VERSION_LINE.sub(f'version: "{version}"', text, count=1))


def write_entry(addon: Addon, version: str, text: str) -> None:
    path = ROOT / addon.changelog
    current = path.read_text() if path.exists() else "# Changelog\n"
    head, sep, rest = current.partition("\n## ")
    bullets = "\n".join(
        line if line.startswith("- ") else f"- {line}"
        for line in (part.strip() for part in text.splitlines())
        if line
    )
    entry = f"## {version}\n\n{bullets}\n"
    body = f"{head.rstrip()}\n\n{entry}" + (f"\n## {rest}" if sep else "")
    path.write_text(body)


def release(addon: Addon, part: str, message: str | None, stage: bool) -> bool:
    """Make the working tree carry a fresh version and its entry. False on no text."""
    manifest_now = (ROOT / addon.manifest).read_text()
    current = version_in(manifest_now)
    if current is None:
        print(f"no version line in {addon.manifest}", file=sys.stderr)
        return False
    head = version_in(head_text(addon.manifest) or "")
    version = current if head is not None and current != head else bumped(current, part)

    changelog_path = ROOT / addon.changelog
    changelog = changelog_path.read_text() if changelog_path.exists() else ""
    if not has_entry(changelog, version):
        text = ask_changelog(addon, version, message)
        if not text:
            return False
        write_entry(addon, version, text)
    if version != current:
        write_version(addon, version)
    if stage:
        git("add", addon.manifest, addon.changelog)
    print(f"{addon.name}: {version}")
    return True


def check(message: str | None) -> int:
    staged = [p for p in git("diff", "--cached", "--name-only").stdout.splitlines() if p]
    failed = []
    for addon in ADDONS:
        touched = [
            p for p in staged
            if p != addon.changelog and any(matches(p, pat) for pat in addon.inputs)
        ]
        if not touched:
            continue
        manifest = staged_text(addon.manifest)
        head = head_text(addon.manifest)
        if manifest is None or head is None:
            continue
        version = version_in(manifest)
        ok_version = version is not None and version != version_in(head)
        ok_entry = version is not None and has_entry(staged_text(addon.changelog) or "", version)
        if ok_version and ok_entry:
            continue
        # Bumping rewrites the working-tree manifest and stages it whole; with
        # unstaged edits there that would commit them behind the owner's back.
        unstaged = git("diff", "--name-only", "--", addon.manifest, addon.changelog).stdout.strip()
        if unstaged:
            print(f"{addon.name}: unstaged changes in {unstaged.replace(chr(10), ', ')}; "
                  "stage or stash them first", file=sys.stderr)
            failed.append(addon)
            continue
        if not release(addon, "patch", message, stage=True):
            failed.append(addon)

    if failed:
        names = " ".join(a.name for a in failed)
        print(
            f"\ncommit refused: {names} changed without a version bump and changelog entry.\n"
            f"Retry with ADDON_CHANGELOG=\"what changed\" git commit ...,\n"
            f"or run tools/addon_release.py bump <addon> -m \"what changed\" first.",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="pre-commit: bump what the staged change needs")
    bump = sub.add_parser("bump", help="bump one add-on by hand")
    bump.add_argument("addon", choices=[a.name for a in ADDONS])
    bump.add_argument("-m", "--message", help="changelog text; one bullet per line")
    part = bump.add_mutually_exclusive_group()
    part.add_argument("--minor", dest="part", action="store_const", const="minor")
    part.add_argument("--major", dest="part", action="store_const", const="major")
    args = parser.parse_args()

    if args.command == "check":
        return check(None)
    addon = next(a for a in ADDONS if a.name == args.addon)
    return 0 if release(addon, args.part or "patch", args.message, stage=False) else 1


if __name__ == "__main__":
    sys.exit(main())
