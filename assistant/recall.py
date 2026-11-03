"""Recall over the raw log and the wiki: what claude-mem was for, in-process.

Two rankings, fused:

  words    SQLite FTS5 (bm25) -- names, numbers, booking codes, anything said
           verbatim. Built into Python's sqlite3, nothing to install.
  meaning  multilingual-e5-small, ONNX, run locally on onnxruntime (already in
           the image for faster-whisper). Finds a paraphrase and an English
           question about an Italian conversation. Nothing leaves the machine.

Merged with reciprocal-rank fusion, then dates: "ieri", "21 agosto", "last
week" restrict to those days; "l'ultima volta" ranks by recency. Nothing dated
after `now` is ever returned, so a question asked "as of" a day cannot see the
days after it.

The unit is an exchange: one owner message and the assistant entries after it
(see tools/memory_eval.py, which scores this module). Long exchanges are cut
into passages for the embedding model; an exchange scores as its best passage.

The index lives in state/ (machine state, unreadable to the model) and is
brought up to date incrementally: a raw file is re-read only when its size or
mtime changed, a wiki page likewise.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

MODEL_REPO = __import__("os").environ.get("RECALL_MODEL", "Xenova/multilingual-e5-small")
MODEL_FILE = "onnx/model_quantized.onnx"
DIM = 768 if "base" in MODEL_REPO else 384
PASSAGE_CHARS = 900
ASSISTANT_ROLE = "assistant"
HEADER = re.compile(r"^### (\w+) — (\d\d:\d\d:\d\d)\s*$", re.M)
RRF_K = 60
POOL = 60
# Results come as k exchanges plus this many wiki pages, in slots of their own:
# a wiki page is the curated truth and must not lose a race to the chat it
# was distilled from.
WIKI_SLOTS = 2
TOP_EACH = int(__import__("os").environ.get("RECALL_TOP_EACH", "2"))
FTS_WEIGHT = float(__import__("os").environ.get("RECALL_FTS_WEIGHT", "0.5"))


# -- units -------------------------------------------------------------------


@dataclass
class Unit:
    id: str
    kind: str          # raw | wiki
    ts: str            # ISO timestamp; wiki pages: their `updated:` day
    text: str


def parse_raw(path: Path) -> list[Unit]:
    day = path.stem
    text = path.read_text(encoding="utf-8")
    heads = list(HEADER.finditer(text))
    units: list[Unit] = []
    current: Unit | None = None
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        role, clock = head.group(1), head.group(2)
        body = text[head.end():end].strip()
        if role != ASSISTANT_ROLE or current is None:
            current = Unit(f"raw:{day}T{clock}", "raw", f"{day}T{clock}", "")
            units.append(current)
        speaker = "assistant" if role == ASSISTANT_ROLE else "owner"
        current.text += f"\n[{speaker} {day} {clock}] {body}"
    return units


_UPDATED = re.compile(r"^updated:\s*(\d{4}-\d\d-\d\d)", re.M)


def parse_wiki(path: Path) -> Unit:
    text = path.read_text(encoding="utf-8")
    m = _UPDATED.search(text)
    ts = f"{m.group(1)}T00:00:00" if m else "1970-01-01T00:00:00"
    return Unit(f"wiki:{path.stem}", "wiki", ts, f"[wiki {path.stem}]\n{text}")


def passages(text: str) -> list[str]:
    """Paragraph-aligned pieces of at most ~PASSAGE_CHARS."""
    out, buf = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if not para:
            continue
        while len(para) > PASSAGE_CHARS:
            if buf:
                out.append(buf)
                buf = ""
            out.append(para[:PASSAGE_CHARS])
            para = para[PASSAGE_CHARS:]
        if len(buf) + len(para) + 1 > PASSAGE_CHARS and buf:
            out.append(buf)
            buf = ""
        buf = f"{buf} {para}".strip()
    if buf:
        out.append(buf)
    return out or [" ".join(text.split())[:PASSAGE_CHARS]]


# -- embedding -----------------------------------------------------------------


class Embedder:
    """multilingual-e5-small: mean pooling, L2-normalised, query/passage prefixes."""

    def __init__(self, models_dir: Path):
        import numpy as np
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        cache = str(models_dir)
        model = hf_hub_download(MODEL_REPO, MODEL_FILE, cache_dir=cache)
        tok = hf_hub_download(MODEL_REPO, "tokenizer.json", cache_dir=cache)
        self.np = np
        self.tokenizer = Tokenizer.from_file(tok)
        self.tokenizer.enable_truncation(512)
        self.tokenizer.enable_padding()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(model, opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}

    def __call__(self, texts: list[str], kind: str, batch: int = 16):
        np = self.np
        prefix = "query: " if kind == "query" else "passage: "
        out = []
        for start in range(0, len(texts), batch):
            enc = self.tokenizer.encode_batch([prefix + t for t in texts[start:start + batch]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            vec = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            vec /= np.linalg.norm(vec, axis=1, keepdims=True)
            out.append(vec.astype(np.float32))
        return np.concatenate(out) if out else np.zeros((0, DIM), np.float32)


# -- dates in the question ---------------------------------------------------------

_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:di\s+|of\s+)?({_MONTH_RE})\b", re.I)
_MONTH_DAY = re.compile(rf"\b({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_RECENT = re.compile(r"\b(l'ultima volta|ultima volta|l'ultima|last time|most recent|più recente|di recente|recently)\b", re.I)
_FIRST = re.compile(r"\b(la prima volta|prima volta|il primo|la prima|first time|the first)\b", re.I)


def _year_for(month: int, day: int, now: date) -> date | None:
    try:
        d = date(now.year, month, day)
    except ValueError:
        return None
    return d if d <= now else date(now.year - 1, month, day)


_REL_DAYS = (r"\b(ieri l'altro|l'altro ieri|altro ieri|day before yesterday|ieri|yesterday|oggi|"
             r"stamattina|stasera|today|this morning|tonight|settimana scorsa|scorsa settimana|"
             r"last week|questa settimana|this week)\b")
_TALK = re.compile(r"\b(abbiamo parlato|ne abbiamo parlato|parlato|discusso|we talked|talked about|"
                   r"discussed|did we discuss|did i send you|ti ho chiesto|mi hai detto)\b", re.I)


def topic(query: str) -> str:
    """The question without its steering: dates, recency, "did we talk about"."""
    q = query
    for pattern in (_RECENT, _FIRST, _DAY_MONTH, _MONTH_DAY, _NUMERIC, _TALK):
        q = pattern.sub(" ", q)
    q = re.sub(_REL_DAYS, " ", q, flags=re.I)
    q = " ".join(q.split())
    return q if len(_TOKEN.findall(q)) >= 1 else query


def date_window(query: str, now: datetime) -> tuple[date, date] | None:
    """The days a question is about, if it names any."""
    q, today = query.lower(), now.date()
    rel = [
        (r"\b(ieri l'altro|l'altro ieri|altro ieri|day before yesterday)\b", 2, 2),
        (r"\b(ieri|yesterday)\b", 1, 1),
        (r"\b(oggi|stamattina|stasera|today|this morning|tonight)\b", 0, 0),
        (r"\b(settimana scorsa|scorsa settimana|last week)\b", 13, 7),
        (r"\b(questa settimana|this week)\b", 6, 0),
    ]
    for pattern, back_from, back_to in rel:
        if re.search(pattern, q):
            return today - timedelta(days=back_from), today - timedelta(days=back_to)
    for m in _DAY_MONTH.finditer(q):
        d = _year_for(_MONTHS[m.group(2).lower()], int(m.group(1)), today)
        if d:
            return d, d
    for m in _MONTH_DAY.finditer(q):
        d = _year_for(_MONTHS[m.group(1).lower()], int(m.group(2)), today)
        if d:
            return d, d
    for m in _NUMERIC.finditer(q):
        day, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            d = _year_for(month, day, today)
            if d:
                return d, d
    for name, month in _MONTHS.items():
        if re.search(rf"\b(inizio|early|beginning of)\s+(di\s+)?{name}\b", q):
            start = _year_for(month, 1, today)
            if start:
                return start, start + timedelta(days=9)
    return None


def _excerpt(text: str, n: int | None, query: str) -> str:
    """The part of a unit worth showing: the owner's message, then the passage
    that matched -- by meaning if the model picked one, else by shared words."""
    parts = passages(text)
    if n is None or n >= len(parts):
        words = {w for w in _TOKEN.findall(query.lower()) if w not in _STOP}
        n = max(range(len(parts)),
                key=lambda i: len(words & set(_TOKEN.findall(parts[i].lower()))))
    if n == 0 or not text.lstrip().startswith("[owner"):
        return parts[n]
    return f"{parts[0][:400]} … {parts[n]}"


# -- the index ---------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sig TEXT);
CREATE TABLE IF NOT EXISTS units (id TEXT PRIMARY KEY, kind TEXT, ts TEXT, source TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS vectors (unit TEXT, n INTEGER, vec BLOB, PRIMARY KEY (unit, n));
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(id UNINDEXED, text, tokenize='unicode61 remove_diacritics 2');
"""

_STOP = set("""
a ad al alla alle allo agli ai anche che chi ci come con cosa da dal dalla dei del della delle
dello di do e è ed gli ha ho i il in io la le lo l ma mi mia mio ne nei nel nella non o per più
poi quale quali quando quanta quante quanti quanto se si sono su sul sulla ti tra tu un una uno
the and or of to in on for at by with is was are were be been do did does what which who whom
how when where why my me i you your we our it its this that these those from about into
abbiamo hai avevo aveva era ero essere fare fatto detto
""".split())
_TOKEN = re.compile(r"\w+", re.U)


def _fts_query(query: str) -> str:
    words = [w for w in _TOKEN.findall(query.lower()) if w not in _STOP and len(w) > 1]
    # Prefix match: "prenot" finds prenotato/prenotazione; Italian has no
    # stemmer in SQLite. Dropping final vowels too was tried: more noise, worse.
    return " OR ".join(f'"{w}"*' if len(w) >= 5 else f'"{w}"' for w in words)


class Recall:
    def __init__(self, root: Path, db_path: Path, models_dir: Path, embed: bool = True):
        self.raw_dir = root / "memory" / "raw"
        self.wiki_dir = root / "memory" / "wiki"
        self.db_path = db_path
        self.models_dir = models_dir
        self.embed_enabled = embed
        self._embedder: Embedder | None = None
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript(_SCHEMA)
        self._matrix = None

    # embedding is optional: without the model, words alone still answer
    def embedder(self) -> Embedder | None:
        if not self.embed_enabled:
            return None
        if self._embedder is None:
            try:
                self._embedder = Embedder(self.models_dir)
            except Exception as exc:  # noqa: BLE001 - any failure means words only
                log.warning("recall: embedding model unavailable (%s); words only", exc)
                self.embed_enabled = False
                return None
        return self._embedder

    def _sources(self):
        for p in sorted(self.raw_dir.glob("*.md")):
            yield p, lambda p=p: parse_raw(p)
        for p in sorted(self.wiki_dir.glob("*.md")):
            if p.stem not in ("index", "log"):
                yield p, lambda p=p: [parse_wiki(p)]

    def refresh(self) -> int:
        """Re-index files whose size or mtime changed; drop vanished ones."""
        with self._lock:
            known = dict(self.db.execute("SELECT path, sig FROM files"))
            seen, changed = set(), 0
            for path, parse in self._sources():
                st = path.stat()
                sig = f"{st.st_size}:{st.st_mtime_ns}"
                key = str(path)
                seen.add(key)
                if known.get(key) == sig:
                    continue
                self._drop(key)
                units = parse()
                self._add(key, units)
                self.db.execute("INSERT OR REPLACE INTO files VALUES (?, ?)", (key, sig))
                changed += 1
            for key in set(known) - seen:
                self._drop(key)
                self.db.execute("DELETE FROM files WHERE path = ?", (key,))
                changed += 1
            self.db.commit()
            if changed:
                self._matrix = None
            return changed

    def _drop(self, source: str) -> None:
        ids = [r[0] for r in self.db.execute("SELECT id FROM units WHERE source = ?", (source,))]
        for uid in ids:
            self.db.execute("DELETE FROM vectors WHERE unit = ?", (uid,))
            self.db.execute("DELETE FROM fts WHERE id = ?", (uid,))
        self.db.execute("DELETE FROM units WHERE source = ?", (source,))

    def _add(self, source: str, units: list[Unit]) -> None:
        emb = self.embedder()
        for u in units:
            self.db.execute("INSERT OR REPLACE INTO units VALUES (?, ?, ?, ?, ?)",
                            (u.id, u.kind, u.ts, source, u.text))
            self.db.execute("INSERT INTO fts (id, text) VALUES (?, ?)", (u.id, u.text))
            if emb is not None:
                vecs = emb(passages(u.text), "passage")
                for n, v in enumerate(vecs):
                    self.db.execute("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?)",
                                    (u.id, n, v.tobytes()))

    def _vectors(self):
        if self._matrix is None:
            import numpy as np
            rows = self.db.execute("SELECT unit, n, vec FROM vectors").fetchall()
            if not rows:
                self._matrix = ([], [], np.zeros((0, DIM), np.float32))
            else:
                self._matrix = ([r[0] for r in rows], [r[1] for r in rows],
                                np.vstack([np.frombuffer(r[2], np.float32) for r in rows]))
        return self._matrix

    # -- search -----------------------------------------------------------------

    def search(self, query: str, k: int = 5, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now()
        cutoff = now.isoformat(timespec="seconds")
        window = date_window(query, now)
        ts = dict(self.db.execute("SELECT id, ts FROM units WHERE ts <= ?", (cutoff,)))

        def allowed(uid: str) -> bool:
            if uid not in ts:
                return False
            if window and uid.startswith("raw:"):
                day = date.fromisoformat(ts[uid][:10])
                return window[0] <= day <= window[1]
            return not window or uid.startswith("wiki:")

        ranked: list[list[str]] = []
        core = topic(query)
        fq = _fts_query(core)
        # "what did we say on 21 August": no topic left, the day is the answer
        bare = window is not None and not fq
        if fq:
            rows = self.db.execute(
                "SELECT id FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT 400", (fq,))
            ranked.append([r[0] for r in rows if allowed(r[0])][:POOL])

        emb = None if bare else self.embedder()
        best_n: dict[str, int] = {}
        if emb is not None:
            ids, ns, matrix = self._vectors()
            if len(ids):
                scores = matrix @ emb([core], "query")[0]
                best: dict[str, float] = {}
                for uid, n, s in zip(ids, ns, scores):
                    if allowed(uid) and s > best.get(uid, -1.0):
                        best[uid] = float(s)
                        best_n[uid] = n
                ranked.append(sorted(best, key=best.get, reverse=True)[:POOL])

        fused: dict[str, float] = {}
        for weight, ranking in zip(self._weights(len(ranked)), ranked):
            for r, uid in enumerate(ranking):
                fused[uid] = fused.get(uid, 0.0) + weight / (RRF_K + r + 1)
        if window and not any(u.startswith("raw:") for u in fused):
            # A named day with nothing matching the words: the day itself is the answer.
            fused.update({uid: 0.0 for uid in ts if allowed(uid) and uid.startswith("raw:")})
        order = sorted(fused, key=lambda u: (fused[u], ts.get(u, "")), reverse=True)

        if _RECENT.search(query) or _FIRST.search(query):
            head = [u for u in order[:max(k * 2, 10)] if u.startswith("raw:")]
            head.sort(key=lambda u: ts[u], reverse=bool(_RECENT.search(query)))
            order = head + [u for u in order if u not in head]

        # Each list's own best answers are kept whatever the fusion says: a
        # paraphrase only the meaning list found must not lose to word noise.
        keep: list[str] = []
        for ranking in ranked:
            for uid in [u for u in ranking if u.startswith("raw:")][:TOP_EACH]:
                if uid not in keep:
                    keep.append(uid)
        raw = keep[:k] + [u for u in order if u.startswith("raw:") and u not in keep]
        raw = sorted(raw[:k], key=lambda u: order.index(u) if u in order else len(order))
        wiki = [u for u in order if u.startswith("wiki:")][:WIKI_SLOTS]
        out = []
        for uid in raw + wiki:
            text = self.db.execute("SELECT text FROM units WHERE id = ?", (uid,)).fetchone()[0]
            out.append({"id": uid, "when": ts[uid], "text": text,
                        "excerpt": _excerpt(text, best_n.get(uid), core)})
        return out

    def search_many(self, queries: list[str], k: int = 5,
                    now: datetime | None = None) -> list[dict]:
        """Several phrasings of one question, merged: each one's best stay in."""
        self.refresh()
        runs = [self.search(q, k, now) for q in queries if q.strip()]
        score: dict[str, float] = {}
        first: dict[str, dict] = {}
        for run in runs:
            for r, hit in enumerate(x for x in run if x["id"].startswith("raw:")):
                score[hit["id"]] = score.get(hit["id"], 0.0) + 1.0 / (RRF_K + r + 1)
                first.setdefault(hit["id"], hit)
            for r, hit in enumerate(x for x in run if x["id"].startswith("wiki:")):
                score[hit["id"]] = score.get(hit["id"], 0.0) + 1.0 / (RRF_K + r + 1)
                first.setdefault(hit["id"], hit)
        keep = []
        for run in runs:
            for hit in [x for x in run if x["id"].startswith("raw:")][:TOP_EACH]:
                if hit["id"] not in keep:
                    keep.append(hit["id"])
        raw = sorted((u for u in score if u.startswith("raw:")), key=score.get, reverse=True)
        raw = (keep + [u for u in raw if u not in keep])[:k]
        raw.sort(key=lambda u: -score[u])
        wiki = sorted((u for u in score if u.startswith("wiki:")), key=score.get,
                      reverse=True)[:WIKI_SLOTS]
        return [first[u] for u in raw + wiki]

    def _weights(self, n: int) -> list[float]:
        # words first, meaning second; with both, meaning leads (see the eval)
        return [FTS_WEIGHT, 1.0] if n == 2 else [1.0] * n


# -- eval adapter (tools/memory_eval.py --retriever assistant.recall:eval_search) --

_EVAL: Recall | None = None


def eval_search(query: str, k: int, now: datetime | None) -> list[str]:
    global _EVAL
    if _EVAL is None:
        import os
        root = Path(os.environ.get("ASSISTANT_ROOT", Path(__file__).resolve().parent.parent))
        words_only = os.environ.get("RECALL_WORDS_ONLY") == "1"
        name = "words" if words_only else MODEL_REPO.split("/")[-1]
        _EVAL = Recall(root, root / "state" / f"recall-{name}.db",
                       root / "models", embed=not words_only)
        _EVAL.refresh()
    if now is None:
        # "as of" the end of the corpus: the moment the questions were written from
        last = _EVAL.db.execute("SELECT max(ts) FROM units WHERE kind = 'raw'").fetchone()[0]
        now = datetime.fromisoformat(last) + timedelta(hours=1)
    return [r["id"] for r in _EVAL.search(query, k, now)]


def context(now: datetime) -> str:
    """Eval hook: the recent-context block the bot gets at `now`."""
    import os

    from .digest import context_block
    root = Path(os.environ.get("ASSISTANT_ROOT", Path(__file__).resolve().parent.parent))
    return context_block(root, now)
