"""`uv run dev lit snowball <seed ids...>` (#591): expand a note's accepted papers into ranked candidates.

#192 found that a first-pass search reaches under 10 % of the works a merged note cites, and that notes reach most of
the rest by narrower searches and citation chasing done by hand (lit-search-quality.md §6, §8). This chases every seed
at once through OpenAlex and ranks what it finds by four signals, each a count over the hop's frontier (the seeds, then
the top candidates of the previous hop):

- link: frontier papers it is directly linked to (a frontier paper cites it, or it cites one);
- cocite: papers citing the frontier that also cite it (co-citation);
- couple: the frontier's references it also cites (bibliographic coupling);
- related: frontier papers whose OpenAlex `related_works` list it.

A hop costs four OpenAlex calls whatever the frontier's size (up to OA_BATCH papers): the frontier's records
(references, related works), its references with their citation counts, the works citing it, and the works citing its
most shared references, each with their reference lists; the printed candidates cost one more call for their records.
Every call goes through devkit.lit's client: the shared cache, rate buckets and backoff.

A candidate's score is a rank fusion of the four signals (fuse). `--depth 2` or 3 makes the top `--keep` candidates of
a hop the next hop's frontier, its scores counting DECAY as much, and stops early when a hop puts no new candidate
into the top n (the pruning threshold is the n-th score). Fusion and defaults were chosen on `dev eval research`'s
questions, half of each note's cited works as seeds (the PR of #591 has the numbers).
"""

import json
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field

from devkit import lit

SIGNALS = ("link", "cocite", "couple", "related")
K = 1  # rank fusion's constant: 1 favours each signal's top ranks (60, the usual web-search value, scored lower here)
DEFAULT_N = 20
MAX_N = lit.OA_BATCH  # the printed candidates' records are one OpenAlex batch
MAX_DEPTH = 3
KEEP = 10  # frontier size of the next hop
DECAY = 0.5  # a hop's scores count this much less than the previous hop's
PER_PAGE = 200  # OpenAlex's largest page
COUPLE_REFS = lit.OA_BATCH  # the frontier's most shared references whose citers are fetched for coupling
SEED_SELECT = "select=id,doi,referenced_works,related_works"
LIST_SELECT = "select=id,cited_by_count,referenced_works"


def wid(url: str | None) -> str:
    """The W-id of an OpenAlex work URL or id."""
    return (url or "").rsplit("/", 1)[-1].upper()


@dataclass
class Candidate:
    wid: str
    score: float = 0.0
    counts: Counter = field(default_factory=Counter)  # per signal, summed over hops
    hop: int = 0  # the first hop that surfaced it
    cites: int = 0  # OpenAlex citation count where an answer gave it (a tie-break)
    paper: lit.Paper | None = None

    def via(self) -> str:
        return "+".join(s for s in SIGNALS if self.counts[s])


def shared_refs(frontier: list[dict]) -> list[str]:
    """The frontier's COUPLE_REFS most shared references; ties by W-id, so the coupling call's URL (its cache key) is
    the same in every process (a set's order is not)."""
    shared = Counter(wid(r) for f in frontier for r in {wid(x) for x in f.get("referenced_works") or []})
    return sorted(shared, key=lambda w: (-shared[w], w))[:COUPLE_REFS]


def signal_counts(
    frontier: list[dict], refs: list[dict], citers: list[dict], coupled: list[dict], exclude: set[str]
) -> tuple[dict[str, Counter], dict[str, int]]:
    """(counts per signal per W-id, citation counts seen) from one hop's answers. Pure. `frontier` holds the frontier's
    records (id, referenced_works, related_works); `refs`, `citers` and `coupled` are works lists (id,
    cited_by_count, referenced_works): the frontier's references, the works citing it, and the works citing its
    shared references. Nothing in `exclude` is counted."""
    front = {wid(f["id"]) for f in frontier}
    skip = exclude | front
    counts: dict[str, Counter] = {}
    cites: dict[str, int] = {}

    def add(w: str, signal: str, k: int = 1) -> None:
        if w and w not in skip:
            counts.setdefault(w, Counter())[signal] += k

    for f in frontier:
        for w in {wid(r) for r in f.get("referenced_works") or []}:
            add(w, "link")
        for w in {wid(r) for r in f.get("related_works") or []}:
            add(w, "related")
    for c in citers:
        own = {wid(r) for r in c.get("referenced_works") or []}
        add(wid(c["id"]), "link", len(own & front) or 1)
        for w in own - front:
            add(w, "cocite")
    top = set(shared_refs(frontier))
    for c in coupled:
        add(wid(c["id"]), "couple", len({wid(r) for r in c.get("referenced_works") or []} & top))
    for c in (*refs, *citers, *coupled):
        if (w := wid(c["id"])) and c.get("cited_by_count") is not None:
            cites[w] = c["cited_by_count"]
    return counts, cites


def fuse(counts: dict[str, Counter], signals: tuple[str, ...] = SIGNALS) -> dict[str, float]:
    """A hop's score per candidate: reciprocal rank fusion over the signals, the sum of 1 / (K + rank in s),
    rank 1 for the highest count and tied counts sharing the better rank. Pure. Ranks, not counts, so co-citation's
    hundreds and related works' ones weigh alike, and a hub cited by every citer leads one signal, not the sum."""
    out = dict.fromkeys(counts, 0.0)
    for s in signals:
        vals = sorted((c[s] for c in counts.values() if c[s] > 0), reverse=True)
        first: dict[int, int] = {}
        for i, v in enumerate(vals):
            first.setdefault(v, i + 1)
        for w, c in counts.items():
            if c[s] > 0:
                out[w] += 1 / (K + first[c[s]])
    return {w: v for w, v in out.items() if v > 0}


class Snowball:
    """One run's OpenAlex calls, counted per hop."""

    def __init__(self, c: lit.Client):
        self.c = c
        self.calls = 0
        self.notes: list[str] = []

    def _get(self, url: str) -> list[dict]:
        self.calls += 1
        try:
            return json.loads(self.c.fetch("openalex", url)).get("results") or []
        except lit.SourceError as exc:
            self.notes.append(str(exc))
        except ValueError as exc:
            self.notes.append(f"openalex: unreadable answer ({type(exc).__name__})")
        return []

    def works(self, filt: str, values: list[str], select: str, sort: bool = False) -> list[dict]:
        """Works matching an OpenAlex OR-filter over `values`, in batches of lit.OA_BATCH."""
        out = []
        for i in range(0, len(values), lit.OA_BATCH):
            q = urllib.parse.quote("|".join(values[i : i + lit.OA_BATCH]), safe="/")
            per = PER_PAGE if sort else min(PER_PAGE, len(values[i : i + lit.OA_BATCH]))
            order = "&sort=cited_by_count:desc" if sort else ""
            out += self._get(f"https://api.openalex.org/works?filter={filt}:{q}{order}&per_page={per}&{select}")
        return out

    def seeds(self, idents: list[str]) -> list[dict]:
        """The seeds' records (W-id, references, related works), in one call per batch of DOIs or W-ids."""
        keys = {}
        for ident in idents:
            target = lit.chase_target(ident)  # an arXiv id becomes its DataCite DOI; ValueError for an S2 id
            keys[ident] = lit.openalex_key(target) or ("doi", target.lower())
        found: dict[tuple[str, str], dict] = {}
        for name in ("doi", "openalex"):
            values = sorted({v for f, v in keys.values() if f == name})
            for w in self.works(name, values, SEED_SELECT) if values else []:
                k = lit._strip_doi(w.get("doi")).lower() if name == "doi" else wid(w["id"])
                found[(name, k)] = w
        out = []
        for ident, k in keys.items():
            if k in found:
                out.append(found[k])
            else:
                self.notes.append(f"seed not in OpenAlex: {ident}")
        return out

    def records(self, wids: list[str]) -> dict[str, lit.Paper]:
        """The printed candidates' records (title, year, venue, ids, open access), one batch call."""
        papers = lit.parse_openalex(json.dumps({"results": self._get(lit.batch_url("openalex", wids))}))
        return {p.ids["openalex"].upper(): p for p in papers}

    def hop(
        self, frontier: list[dict], exclude: set[str], couple: bool = True
    ) -> tuple[dict[str, Counter], dict[str, int]]:
        """One hop's signal counts: the frontier's references (for their citation counts), its citers and, for
        coupling, the citers of its most shared references, one call each."""
        ids = sorted({wid(f["id"]) for f in frontier})
        refs = self.works("cited_by", ids, LIST_SELECT, sort=True)
        citers = self.works("cites", ids, LIST_SELECT, sort=True)
        top = sorted(shared_refs(frontier))
        coupled = self.works("cites", top, LIST_SELECT, sort=True) if top and couple else []
        return signal_counts(frontier, refs, citers, coupled, exclude)


def snowball(
    c: lit.Client,
    idents: list[str],
    n: int = DEFAULT_N,
    depth: int = 1,
    signals: tuple[str, ...] = SIGNALS,
    keep: int = KEEP,
) -> tuple[list[Candidate], list[str]]:
    """(the top n candidates, best first, with their records; notes: each hop's OpenAlex calls and how many new
    candidates it put in the top n, then failures)."""
    if not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"--depth is 1 to {MAX_DEPTH}, not {depth}")
    if bad := [s for s in signals if s not in SIGNALS]:
        raise ValueError(f"unknown signal {bad[0]!r}; one of {', '.join(SIGNALS)}")
    n = max(1, min(n, MAX_N))
    sb = Snowball(c)
    exclude = {i.strip().upper() for i in idents if lit.openalex_key(i) == ("openalex", i.strip().upper())}
    cands: dict[str, Candidate] = {}
    hops: list[str] = []
    for h in range(1, depth + 1):
        before = sb.calls
        if h == 1:
            frontier = sb.seeds(idents)
        else:
            nxt = [x.wid for x in ranked(cands) if x.wid not in exclude][:keep]
            frontier = sb.works("openalex", nxt, SEED_SELECT) if nxt else []
        exclude |= {wid(f["id"]) for f in frontier}
        if not frontier:
            break
        counts, cites = sb.hop(frontier, exclude, "couple" in signals)
        for w, v in fuse(counts, signals).items():
            cand = cands.setdefault(w, Candidate(w, hop=h))
            cand.score += v * DECAY ** (h - 1)
            cand.counts.update({k: x for k, x in counts[w].items() if k in signals})
            cand.cites = max(cand.cites, cites.get(w, 0))
        new = sum(x.hop == h for x in ranked(cands)[:n])
        hops.append(f"hop {h}: {sb.calls - before} OpenAlex calls, {new} new in the top {n}")
        if not new:  # pruning: a hop that moved nothing into the output ends the expansion
            break
    top = ranked(cands)[:n]
    if top:
        before = sb.calls
        papers = sb.records([x.wid for x in top])
        for x in top:
            x.paper = papers.get(x.wid)
        hops.append(f"records: {sb.calls - before} OpenAlex call")
    return top, [*hops, f"{sb.calls} OpenAlex calls in all", *sb.notes]


def ranked(cands: dict[str, Candidate]) -> list[Candidate]:
    return sorted(cands.values(), key=lambda x: (-x.score, -x.cites, x.wid))


def line(x: Candidate) -> str:
    p = x.paper or lit.Paper(title="(no record)")
    meta = ", ".join(v for v in (str(p.year or "n.d."), p.venue[:60]) if v)
    ids = " ".join(f"{k}:{v}" for k, v in {"openalex": x.wid, "doi": p.ids.get("doi", "")}.items() if v)
    return (
        f"- {p.title[:160]} ({meta}) [{ids}] score {x.score:.2f}, co-cited {x.counts['cocite']}, "
        f"OA {'yes' if p.oa_url else 'no'}, via {x.via()}"
    )


def render(cands: list[Candidate], notes: list[str]) -> str:
    lines = [line(x) for x in cands] or ["no candidates"]
    return "\n".join([*lines, *(f"note: {n}" for n in notes)])
