"""The polling offset survives a restart, so a crashing update is skipped."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.telegram import Telegram  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        state = Path(d)
        t = Telegram("tok", {1}, state_dir=state)
        assert t._offset is None
        t._offset = 42
        t._save_offset()
        assert Telegram("tok", {1}, state_dir=state)._offset == 42
        (state / "telegram_offset").write_text("junk")
        assert Telegram("tok", {1}, state_dir=state)._offset is None
    assert Telegram("tok", {1})._offset is None
    print("ok")


if __name__ == "__main__":
    main()
