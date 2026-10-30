"""Brief changes the model proposes and the owner accepts in chat.

context/ is the owner's: the model applies it and cannot write it, which is
what keeps a single injected instruction from rewriting the rules every later
turn follows. The cost was that every change to a brief -- the coach's intake,
a shipping country, a calendar to surface -- ended with "copy these lines into
context/..." and a trip to a file share.

Now the model writes the whole new brief to memory/wiki/proposals/<name>.md,
inside its own write zone, and the owner decides with commands the transport
handles in Python (assistant/main.py), the same way /enable decides a routine:

  /review <name>   the diff against the brief in force, and a fingerprint
  /accept <name>   applies it -- only if it is still the text reviewed
  /reject <name>   throws it away

The fingerprint is the point of /review: a proposal edited after the owner
read it is not the proposal they accepted. Accepting writes the authoritative
copy -- the add-on config share, from where the supervisor syncs it into the
read-only context/ the model reads -- and never the model's copy.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

PROPOSALS = Path("memory") / "wiki" / "proposals"
REVIEWED = "reviewed.json"

# The same marker scripts/onboard.py fills: a brief line nobody answered yet.
TO_FILL = re.compile(r"\*\*TO FILL\*\*|`TO FILL`")
_UPDATED = re.compile(r"^updated:.*$", re.M)
# Slugs only: the name ends up in a path on the share.
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ProposalError(Exception):
    """Something the owner should read, as is."""


def _name(raw: str) -> str:
    name = raw.strip().lower().removesuffix(".md")
    if not _NAME.match(name):
        raise ProposalError(f"`{raw}` is not a brief name. See /proposals.")
    return name


def _proposal(root: Path, name: str) -> Path:
    path = root / PROPOSALS / f"{name}.md"
    if not path.is_file():
        raise ProposalError(f"No open proposal for `{name}`. See /proposals.")
    return path


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def list_open(root: Path) -> list[str]:
    folder = root / PROPOSALS
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.md") if p.is_file())


def open_blanks(root: Path) -> int:
    """`TO FILL` lines still open across the briefs in force."""
    folder = root / "context"
    if not folder.is_dir():
        return 0
    return sum(len(TO_FILL.findall(p.read_text(encoding="utf-8")))
               for p in folder.glob("*.md"))


def diff(root: Path, raw: str) -> tuple[str, str]:
    """The unified diff of brief -> proposal, and the proposal's fingerprint."""
    name = _name(raw)
    proposed = _proposal(root, name).read_text(encoding="utf-8")
    current_path = root / "context" / f"{name}.md"
    current = current_path.read_text(encoding="utf-8") if current_path.is_file() else ""
    lines = difflib.unified_diff(
        current.splitlines(keepends=True), proposed.splitlines(keepends=True),
        fromfile=f"context/{name}.md", tofile=f"proposal/{name}.md", n=1,
    )
    return "".join(lines), digest(proposed)


def remember_review(state_dir: Path, raw: str, fingerprint: str) -> None:
    reviewed = _reviewed(state_dir)
    reviewed[_name(raw)] = fingerprint
    _write_json(state_dir / REVIEWED, reviewed)


def apply(root: Path, state_dir: Path, target: Path, raw: str) -> str:
    """Write the reviewed proposal over `target`/<name>.md; return the name."""
    name = _name(raw)
    path = _proposal(root, name)
    text = path.read_text(encoding="utf-8")
    if _reviewed(state_dir).get(name) != digest(text):
        raise ProposalError(
            f"`{name}` has not been reviewed as it stands now. "
            f"`/review {name}` first, then `/accept {name}`."
        )
    _validate(name, text)
    if not (root / "context" / f"{name}.md").is_file():
        raise ProposalError(f"There is no brief `{name}` to replace.")
    text = _UPDATED.sub(f"updated: {date.today().isoformat()}", text, count=1)
    target.mkdir(parents=True, exist_ok=True)
    out = target / f"{name}.md"
    tmp = out.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)
    _forget(root, state_dir, name)
    return name


def reject(root: Path, state_dir: Path, raw: str) -> str:
    name = _name(raw)
    _proposal(root, name)
    _forget(root, state_dir, name)
    return name


def _validate(name: str, text: str) -> None:
    """A whole brief, not a fragment: frontmatter with its own name."""
    if not text.startswith("---\n"):
        raise ProposalError(
            f"`{name}` does not start with frontmatter (`---`). Ask me to "
            "write the whole brief, not only the changed lines."
        )
    end = text.find("\n---", 4)
    front = text[4:end] if end != -1 else ""
    if not re.search(rf"^name:\s*{re.escape(name)}\s*$", front, re.M):
        raise ProposalError(f"`{name}`'s frontmatter does not say `name: {name}`.")
    if not text[end + 4:].strip():
        raise ProposalError(f"`{name}` has frontmatter and nothing else.")


def _reviewed(state_dir: Path) -> dict[str, str]:
    try:
        return json.loads((state_dir / REVIEWED).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _forget(root: Path, state_dir: Path, name: str) -> None:
    (root / PROPOSALS / f"{name}.md").unlink(missing_ok=True)
    reviewed = _reviewed(state_dir)
    if reviewed.pop(name, None) is not None:
        _write_json(state_dir / REVIEWED, reviewed)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
