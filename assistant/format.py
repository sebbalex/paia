"""The model's markdown -> HTML that Telegram accepts.

Telegram does not render markdown unless asked, and the two available
parse_modes are not equivalent. MarkdownV2 demands escaping _*[]()~>#+-=|{}.!
everywhere: a file name like test_policy.py, or a list with dashes, makes
parsing fail, and a message that fails the parser is simply not delivered.
Telegram's HTML has a far smaller surface -- only b, i, u, s, code, pre, a,
blockquote -- and needs three characters escaped, so it is the one to rely on.

Callers still have to handle failure: send() re-sends as plain text if
Telegram rejects the markup anyway.
"""

from __future__ import annotations

import html
import re

# Bold uses **, italic *. The underscore is deliberately ignored: the model
# writes names like snake_case_file.py and mcp__mcp-search__search constantly,
# and treating it as italic would break them in half.
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_STRIKE = re.compile(r"~~(.+?)~~", re.S)
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*$", re.M)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.M)
_HRULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$", re.M)
_FENCE = re.compile(r"```([^\n`]*)\n?(.*?)```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def to_telegram_html(text: str) -> str:
    """Convert markdown into Telegram-safe HTML."""
    placeholders: list[str] = []

    def stash(rendered: str) -> str:
        placeholders.append(rendered)
        return f"\x00{len(placeholders) - 1}\x00"

    # Code blocks leave the stage first, or the asterisks and dashes they
    # contain would be read as formatting.
    def fence(match: re.Match) -> str:
        lang = match.group(1).strip()
        body = html.escape(match.group(2).rstrip("\n"))
        if lang:
            return stash(
                f'<pre><code class="language-{html.escape(lang)}">{body}</code></pre>'
            )
        return stash(f"<pre>{body}</pre>")

    text = _FENCE.sub(fence, text)
    text = _INLINE_CODE.sub(
        lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), text
    )
    text = _render_tables(text, stash)

    text = html.escape(text)
    text = _HRULE.sub("─" * 12, text)
    text = _HEADER.sub(r"<b>\1</b>", text)
    text = _BULLET.sub(r"\1• ", text)
    text = _LINK.sub(r'<a href="\2">\1</a>', text)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)

    for index, rendered in enumerate(placeholders):
        text = text.replace(f"\x00{index}\x00", rendered)
    return text.strip()


def _render_tables(text: str, stash) -> str:
    """Markdown tables don't exist on Telegram: they become fixed blocks.

    Hand-aligned inside <pre> they stay readable; left as they are they turn
    into pipe-laden lines wrapping at random.
    """
    lines = text.split("\n")
    out: list[str] = []
    block: list[str] = []

    def flush() -> None:
        if not block:
            return
        rows = [
            [c.strip() for c in row.strip().strip("|").split("|")]
            for row in block
            # the |---|---| separator row carries no content
            if not re.fullmatch(r"[\s|:-]+", row)
        ]
        if rows:
            widths = [
                max(len(r[i]) for r in rows if i < len(r))
                for i in range(max(len(r) for r in rows))
            ]
            rendered = "\n".join(
                "  ".join(
                    cell.ljust(widths[i]) for i, cell in enumerate(row)
                ).rstrip()
                for row in rows
            )
            out.append(stash(f"<pre>{html.escape(rendered)}</pre>"))
        block.clear()

    for line in lines:
        if _TABLE_ROW.match(line):
            block.append(line)
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out)
