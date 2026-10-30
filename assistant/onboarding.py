"""The first conversation, started by the transport instead of pasted.

ONBOARDING.md used to hand the owner a paragraph to paste as their first
message. Now pairing starts it, and /onboard restarts it, optionally on one
topic. The prompt lives beside this file (onboarding.md), so it ships with
the image and the model cannot edit it.

Topics are the briefs actually in context/, not a list kept here: a brief
the owner adds, or one an upgrade ships, joins the interview without a code
change. ROT.md's order goes first because it is the order the owner thinks
in; anything else follows alphabetically.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

from .proposals import open_blanks

TEMPLATE = Path(__file__).with_name("onboarding.md")
SUFFIX = "-rules"

# ROT.md's table, then calendar: the order of the interview.
ORDER = ("shopping", "daily-tasks", "home-duties", "watchlist", "adventures",
         "training", "calendar")


def topics(root: Path) -> list[str]:
    """The topics there is a brief for, in interview order."""
    folder = root / "context"
    if not folder.is_dir():
        return []
    found = {p.stem.removesuffix(SUFFIX) for p in folder.glob(f"*{SUFFIX}.md")
             if p.is_file()}
    known = [t for t in ORDER if t in found]
    return known + sorted(found - set(known))


def match(root: Path, raw: str) -> str | None:
    """The topic `raw` names -- `training`, `training-rules`, `Daily tasks` --
    or None when there is no brief for it."""
    wanted = raw.strip().lower().removesuffix(".md").removesuffix(SUFFIX)
    wanted = "-".join(wanted.replace("_", " ").split())
    return wanted if wanted in topics(root) else None


def prompt(root: Path, topic: str | None = None) -> str:
    """The onboarding prompt: every topic, or only `topic`."""
    all_topics = topics(root)
    chosen = [topic] if topic else all_topics
    focus = (f"Only the topic `{topic}` this time; the others are not part of it."
             if topic else
             "Interview me one topic at a time and file what I answer as you go.")
    return Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        focus=focus,
        topics=", ".join(f"`{t}`" for t in chosen) or "(none: there are no briefs in context/)",
        blanks=open_blanks(root),
    )
