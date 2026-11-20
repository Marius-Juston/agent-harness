"""Related issues for `board new`: BM25 over every earlier issue's title, Context and DONE WHEN."""

import math
import re
from collections import Counter

from devkit import board_gh, board_issue

# Related issues for `board new`: BM25 over title, Context and DONE WHEN of every earlier issue in any state, measured
# in docs/research/issue-pr-automation.md §3.3 and chosen there over TF-IDF and three embedding models. Advisory
# only, never a gate: on DEV about 1 in 3 suggestions names an issue the filer had linked by hand.
RELATED_SHOWN = 3
BM25_K1, BM25_B = 1.5, 0.75  # Lucene's defaults; no k1/b cell beat them on DEV (r2-bm25-variants.md)
# A raw BM25 score, tuned on DEV (dedup_eval.py ship-dev): score / the query's self-score lost 0.061 of alert recall@3
# there, and a 30-day window of closed issues dropped nothing on boards 10 days old. Raw scores grow with the corpus,
# so the threshold is re-measured as the board grows.
RELATED_MIN = 30.26
RELATED_NOTE = "suggested by `board new` from similar text; delete any that are unrelated"
NUMBER_TOKEN = re.compile(r"[\w.-]*#\d+")
CHECKBOX = re.compile(r"^\s*-\s*\[[ xX]\]\s*", re.M)
WORD = re.compile(r"\b\w\w+\b")
# scikit-learn's ENGLISH_STOP_WORDS (BSD-3-Clause), the list the ranker was measured with, less its one-letter words
# (WORD skips them) and its one misspelling of "amongst".
STOP_WORDS = frozenset(
    """about above across after afterwards again against all almost alone along already also although always am among
    amongst amount an and another any anyhow anyone anything anyway anywhere are around as at back be became
    because become becomes becoming been before beforehand behind being below beside besides between beyond bill both
    bottom but by call can cannot cant co con could couldnt cry de describe detail do done down due during each eg
    eight either eleven else elsewhere empty enough etc even ever every everyone everything everywhere except few
    fifteen fifty fill find fire first five for former formerly forty found four from front full further get give go
    had has hasnt have he hence her here hereafter hereby herein hereupon hers herself him himself his how however
    hundred ie if in inc indeed interest into is it its itself keep last latter latterly least less ltd made many may
    me meanwhile might mill mine more moreover most mostly move much must my myself name namely neither never
    nevertheless next nine no nobody none noone nor not nothing now nowhere of off often on once one only onto or
    other others otherwise our ours ourselves out over own part per perhaps please put rather re same see seem seemed
    seeming seems serious several she should show side since sincere six sixty so some somehow someone something
    sometime sometimes somewhere still such system take ten than that the their them themselves then thence there
    thereafter thereby therefore therein thereupon these they thick thin third this those though three through
    throughout thru thus to together too top toward towards twelve twenty two un under until up upon us very via was
    we well were what whatever when whence whenever where whereafter whereas whereby wherein whereupon wherever
    whether which while whither who whoever whole whom whose why will with within without would yet you your yours
    yourself yourselves""".split()  # noqa: SIM905
)


def terms(text: str) -> list[str]:
    """The words BM25 indexes: lowercased, two letters or more, no stop words. `#N` references are cut first (they
    would match on numbers, not topics), with checkboxes and code marks."""
    text = CHECKBOX.sub("", NUMBER_TOKEN.sub(" ", text or "")).replace("`", " ")
    return [w for w in WORD.findall(text.lower()) if w not in STOP_WORDS]


def bm25(docs: list[list[str]], query: list[str], k1: float = BM25_K1, b: float = BM25_B) -> list[float]:
    """Each doc's BM25 score for `query`, with Lucene's idf, as bm25s computes it (a repeated query word counts again).
    It gave bm25s's top 3 on 789/789 queries (r1-dedup.md F7)."""
    n = len(docs)
    if not n:
        return []
    avg = sum(map(len, docs)) / n or 1.0
    df = Counter(w for d in docs for w in set(d))
    idf = {w: math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) for w in set(query) if df[w]}

    def score(d: list[str]) -> float:
        tf, norm = Counter(d), k1 * (1 - b + b * len(d) / avg)
        return sum(idf[w] * tf[w] / (tf[w] + norm) for w in query if w in idf and tf[w])

    return [score(d) for d in docs]


def issue_terms(title: str, context: str, done: str) -> list[str]:
    return terms(title) + terms(context) + terms(done)


def related(
    title: str,
    context: str,
    done: list[str],
    issues: list[board_issue.Issue],
    named: set[int],
    minimum: float | None = None,
) -> list[tuple[board_issue.Issue, float]]:
    """Up to RELATED_SHOWN of `issues` most like a new item, best first, each scoring at least `minimum` (RELATED_MIN
    when None). `named`: issues the filer already names, never suggested."""
    pool = [i for i in issues if i.number not in named]
    docs = [issue_terms(i.title, i.parts.get("Context", ""), i.parts.get("DONE WHEN", "")) for i in pool]
    scores = bm25(docs, issue_terms(title, context, "\n".join(done)))
    least = RELATED_MIN if minimum is None else minimum
    ranked = sorted(zip(scores, pool, strict=True), key=lambda x: (-x[0], x[1].number))
    return [(i, s) for s, i in ranked[:RELATED_SHOWN] if s > 0 and s >= least]


def related_pool() -> list[board_issue.Issue]:
    """Every issue in any state, closed as not planned included (most known duplicate targets were; every measured
    number ranked all of them), in one `gh` try: an advisory read never waits out a rate limit."""
    tries, board_gh.GH_TRIES = board_gh.GH_TRIES, 1  # gh reads it per call; restored below
    try:
        args = ("issue", "list", "-R", board_gh.repo(), "--state", "all", "--limit", str(board_issue.ISSUE_LIMIT))
        rows = board_gh.gh_json(*args, fields=board_issue.FIELDS, links="")
    finally:
        board_gh.GH_TRIES = tries
    return [board_issue._issue(d) for d in rows]


def one_line(text: str, width: int = 100) -> str:
    """Text from GitHub as one printable line: whitespace and control characters collapsed, cut to `width`."""
    flat = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def related_lines(found: list[tuple[board_issue.Issue, float]]) -> list[str]:
    if not found:
        return []
    shown = [f"  #{i.number} {one_line(i.title)}" for i, _ in found]
    return ["Related: " + " ".join(f"#{i.number}" for i, _ in found), *shown]
