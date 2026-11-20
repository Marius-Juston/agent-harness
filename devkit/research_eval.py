"""`uv run dev eval research` (#590): a known-answer evaluation of research, so a change to the research skill, its
sources or its tools is measured like a gate change.

The set (<research_eval_dir>/questions.yaml, [tool.devkit]) is a fixed list of questions, each with its merged answer
note and the 8 opening queries a fresh model wrote from the question alone; gold.json holds the works each note cites.

Deterministic part, no model: each question's queries go through `dev lit search -n 20` (devkit.lit, `--source` as
given), then one snowball hop (`dev lit refs` and `cited-by`, 20 each) from the first SEEDS distinct results in
round-robin rank order. Scored against the gold: recall@10 and @20 (a work counts when any query ranks it that high,
lit-search-quality.md §2), snowball recall (search@20 plus the hop), and the tokens an agent reads to see it all. Every
HTTP answer comes from a replay cassette in the state dir, so the same code reads the same answers; a URL not yet
recorded is fetched live and recorded, and the record counts those, per source; `--offline` fetches nothing.

Held-out snowball arm (#619, #591's replay): a seeded half of each note's cited works with an OpenAlex id are the
seeds of `dev lit snowball -n 20` (devkit.snowball) at depth 1 and 2, with all four signals and with direct links
only; each arm's recall is over the other half (held out), next to the search lists' recall@20 of that half, with the
OpenAlex calls each arm cost.

Sampled part, one agent call per claim: `--claims K` draws K claims per question (seeded) from its note, or from the
note of the same name in `--notes DIR` (a candidate research run's output), and a tool-limited `claude -p` auditor
judges whether the cited source supports each, with the research-auditor's verdicts. Records go to
<state>/evals/research/run-<epoch>.json; `--compare A B` pairs two by question.
"""

import hashlib
import json
import os
import random
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from devkit import config, evals, lit, snowball
from devkit.netsafe import atomic_write
from devkit.paths import git, root, state_dir, user_state_dir

N = 20  # results per query: `dev lit search -n 20`, #192's L0
KS = (10, 20)
SEEDS = 5  # snowball seeds per question
CHASE_N = 20  # works per refs or cited-by call
SEED = 590  # claim sampling
DRAWS = 10_000
HELD_N = 20  # snowball candidates scored against the held-out half
# The held-out arms: (name, depth, signals); "link" is snowball without its neighbour signals.
HELD_ARMS = (
    ("d1", 1, snowball.SIGNALS),
    ("d1 link", 1, ("link",)),
    ("d2", 2, snowball.SIGNALS),
    ("d2 link", 2, ("link",)),
)
VERDICTS = ("supported", "partly", "unsupported", "unsourced", "unreachable")
AUDIT_TOOLS = ["WebFetch", "Bash(uv run dev lit get *)", "Bash(uv run --quiet dev lit get *)"]
AUDIT_PROMPT = """You audit ONE claim from a research note. Do not edit anything.

Claim (one line of the note, with its links):
{claim}

Fetch the cited source: for a paper run `uv run dev lit get <doi or arXiv id>` (never curl or WebFetch OpenAlex,
Crossref, Semantic Scholar or Europe PMC); for anything else use WebFetch. Decide, as the research-auditor does,
whether the source supports the claim (supported), partly supports it (overstated, wrong population or setting, wrong
number: partly), does not support it (unsupported), or the line has no source for its claim (unsourced); use
unreachable only when no cited source could be fetched. Use at most a few fetches.

End your reply with exactly one line: VERDICT: supported|partly|unsupported|unsourced|unreachable"""


@dataclass
class Replay(lit.Client):
    """A lit.Client whose answers come from a cassette (one file per URL, API keys never in the key): a recorded URL
    is replayed without a call or a rate-limit wait; any other is fetched live and recorded, or refused offline. A
    failure (a source down or throttled) is recorded too and replays as the same failure, so a rerun sees exactly the
    sources the recording saw; deleting the cassette directory records afresh."""

    cassette: Path = Path()
    offline: bool = False
    counts: dict[str, int] = field(
        default_factory=lambda: {"replayed": 0, "live": 0, "missing": 0, "failed": 0, "replayed_failures": 0}
    )
    by_source: dict[str, dict[str, int]] = field(default_factory=dict)  # the same counts per source

    def _tape(self, url: str) -> Path:
        return self.cassette / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")

    def _count(self, source: str, kind: str) -> None:
        self.counts[kind] += 1
        per = self.by_source.setdefault(source, dict.fromkeys(self.counts, 0))
        per[kind] += 1

    def calls(self, source: str) -> int:
        """Every answer `source` gave this run, replayed or live, failures included: the calls the code made."""
        return sum(self.by_source.get(source, {}).values())

    def fetch(self, source, url, *, live=False, tries=lit.TRIES, max_age=lit.CACHE_DAYS * 86400):
        tape = self._tape(url)
        if tape.is_file():
            rec = json.loads(tape.read_text())
            if "error" in rec:
                self._count(source, "replayed_failures")
                raise lit.SourceError(rec["error"])
            self._count(source, "replayed")
            return rec["body"]
        if self.offline:
            self._count(source, "missing")
            raise lit.SourceError(f"{source}: not in the replay cassette (--offline)")
        self.cassette.mkdir(parents=True, exist_ok=True)
        meta = {"url": url, "source": source, "t": self.clock()}
        try:
            body = super().fetch(source, url, live=True, tries=tries)
        except lit.SourceError as exc:
            self._count(source, "failed")
            atomic_write(tape, json.dumps({**meta, "error": str(exc)}))
            raise
        atomic_write(tape, json.dumps({**meta, "body": body}))
        self._count(source, "live")
        return body


def load_set(spec_dir: Path) -> tuple[list[dict], dict[str, list[dict]]]:
    """(questions, gold per question id) of a question set."""
    questions = yaml.safe_load((spec_dir / "questions.yaml").read_text())["questions"]
    gold = json.loads((spec_dir / "gold.json").read_text())
    return questions, {q["id"]: gold[q["id"]] for q in questions}


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def _ids(p: lit.Paper) -> set[tuple[str, str]]:
    out = set()
    for k, v in p.ids.items():
        if v and k in ("doi", "arxiv", "openalex", "pmid", "pmcid"):
            v = v.lower() if k == "doi" else re.sub(r"v\d+$", "", v) if k == "arxiv" else v.upper()
            out.add((k, v))
    return out


class Matcher:
    """#192's rules for results with ids (lit-search-quality.md §2): any shared identifier, else the normalised title
    or one of the note's link texts for the work (equal, or one inside the other when both are 30+ characters)."""

    def __init__(self, gold: list[dict]):
        self.by_id: dict[tuple[str, str], str] = {}
        for g in gold:
            for k, v in g["ids"].items():
                if v:
                    self.by_id[(k, v.lower() if k == "doi" else v.upper() if k in ("openalex", "pmcid") else v)] = g[
                        "key"
                    ]
        self.titles = [(t, g["key"]) for g in gold for x in [g["title"], *g.get("alt", [])] if (t := _norm_title(x))]

    def match(self, p: lit.Paper) -> str | None:
        if hit := next((self.by_id[i] for i in _ids(p) if i in self.by_id), None):
            return hit
        t = _norm_title(p.title)
        if not t:
            return None
        return next((k for g, k in self.titles if t == g or (min(len(t), len(g)) >= 30 and (t in g or g in t))), None)


def tokens(text: str) -> int:
    """Tokens an agent reads, estimated at 4 characters per token (no tokenizer call: the count stays offline)."""
    return (len(text) + 3) // 4


def seeds(lists: list[list[lit.Paper]], k: int = SEEDS) -> list[lit.Paper]:
    """The first k distinct papers with an OpenAlex id, taking rank 1 of every query, then rank 2, and so on."""
    out: dict[str, lit.Paper] = {}
    for i in range(max(map(len, lists), default=0)):
        for papers in lists:
            if i < len(papers) and papers[i].ids.get("openalex"):
                out.setdefault(papers[i].key(), papers[i])
    return list(out.values())[: max(0, k)]


def score_question(c: lit.Client, q: dict, gold: list[dict], source: str = "auto") -> dict:
    """The deterministic metrics of one question: search recall@k, snowball recall and tokens read."""
    m = Matcher(gold)
    sources, fallback = lit.plan(source)
    lists, read, notes = [], 0, 0
    for query in q["queries"]:
        papers, got = c.search(query, sources, N, fallback=fallback)
        lists.append(papers)
        read += tokens(lit.render(papers, got))
        notes += len(got)
    found = {k: sorted({h for papers in lists for p in papers[:k] if (h := m.match(p))}) for k in KS}
    chased: set[str] = set()
    for p in seeds(lists, SEEDS):
        for direction in ("refs", "cited-by"):
            papers, got = c.chase(p.ids["openalex"], direction, CHASE_N)
            read += tokens(lit.render(papers, got))
            chased |= {h for x in papers if (h := m.match(x))}
    snow = sorted(set(found[N]) | chased)
    n = len(gold)
    return {
        "gold": n,
        **{f"recall@{k}": round(len(found[k]) / n, 4) if n else 0.0 for k in KS},
        "snowball": round(len(snow) / n, 4) if n else 0.0,
        "found@20": found[N],
        "snowball_found": snow,
        "read_tokens": read,
        "source_notes": notes,
    }


def held_out(gold: list[dict], qid: str, seed: int = SEED) -> tuple[list[str], list[dict]]:
    """(seed W-ids, held-out gold): a seeded half (rounded down) of the gold works with an OpenAlex id, and the rest of
    them. Works without one are left out of both: a snowball can neither start from nor be scored on them alike."""
    with_w = [g for g in gold if g["ids"].get("openalex")]
    order = random.Random(f"{seed}:{qid}:held-out").sample(with_w, len(with_w))  # noqa: S311 - a seeded split
    half = len(order) // 2
    return [g["ids"]["openalex"].upper() for g in order[:half]], order[half:]


def score_held_out(c: Replay, qid: str, gold: list[dict], found: list[str]) -> dict:
    """Held-out recall of each snowball arm's top HELD_N and of the search lists (`found`: the gold keys any query
    ranked in its top 20), and the OpenAlex calls each arm made."""
    seed_ids, held = held_out(gold, qid)
    m, n = Matcher(held), len(held)
    keys = {g["key"] for g in held}

    def share(hits: set[str]) -> float:
        return round(len(hits) / n, 4) if n else 0.0

    row = {"held_seeds": len(seed_ids), "held": n, "held search@20": share(keys & set(found))}
    for name, depth, signals in HELD_ARMS:
        before = c.calls("openalex")
        cands = snowball.snowball(c, seed_ids, HELD_N, depth, signals)[0] if seed_ids and n else []
        row[f"held@20 {name}"] = share(
            {h for x in cands if (h := m.match(x.paper or lit.Paper("", ids={"openalex": x.wid})))}
        )
        row[f"calls {name}"] = c.calls("openalex") - before
    return row


def claims(text: str) -> list[str]:
    """Lines of a note that make a claim and cite a web source: a markdown link to http(s), outside the Sources
    list, headings and tables' separator rows."""
    out, skip = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            skip = line.lstrip("#").strip().lower().startswith(("sources", "references"))
            continue
        s = line.strip()
        if not skip and re.search(r"\]\(https?://", s) and not re.fullmatch(r"[|\s:-]+", s):
            out.append(s[:800])
    return out


def sample_claims(text: str, k: int, qid: str, seed: int = SEED) -> list[str]:
    cands = claims(text)
    return random.Random(f"{seed}:{qid}").sample(cands, min(k, len(cands)))  # noqa: S311 - a seeded sample


def verdict(final: str) -> str:
    hits = re.findall(r"VERDICT:\s*\**\s*(" + "|".join(VERDICTS) + r")\b", final or "", re.I)
    return hits[-1].lower() if hits else "none"


def audit_command(claim: str, model: str = "") -> list[str]:
    cmd = [
        "claude",
        "-p",
        AUDIT_PROMPT.format(claim=claim),
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--max-turns",
        "12",
        "--strict-mcp-config",
        "--tools",
        "Bash,WebFetch",
        "--allowedTools",
        ",".join(AUDIT_TOOLS),
    ]
    return cmd + (["--model", model] if model else [])


def audit(claim: str, model: str = "", runner=subprocess.run, cwd: Path | None = None) -> dict:
    """One auditor call on one claim: its verdict, billed tokens and cost (evals.parse reads the transcript)."""
    try:
        out = runner(
            audit_command(claim, model),
            cwd=cwd or root(),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            env=evals.trial_env(dict(os.environ), {}),
        )
        t = evals.parse(out.stdout.splitlines())
    except subprocess.TimeoutExpired:
        t = evals.parse([])
    return {
        "claim": claim,
        "verdict": verdict(t["final"]),
        "tokens": sum(t["tokens"].values()),
        "cost_usd": round(t["cost"], 4),
        "models": t["models"],
    }


def supported_share(audits: list[dict]) -> float | None:
    """Supported claims over judged ones (unreachable and unparsed verdicts are not judged)."""
    judged = [a for a in audits if a["verdict"] in VERDICTS[:4]]
    return round(sum(a["verdict"] == "supported" for a in judged) / len(judged), 4) if judged else None


def run(
    label: str = "",
    n_claims: int = 2,
    model: str = "",
    source: str = "auto",
    offline: bool = False,
    notes_dir: str = "",
    max_usd: float = 5.0,
    *,
    spec_dir: Path | None = None,
    base: Path | None = None,
    state: Path | None = None,
    get: lit.Get = lit.http_get,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    runner=subprocess.run,
    commit: str = "",
) -> int:
    """Run the eval and write its record; everything outside (HTTP, `claude`, clock, dirs) is injectable."""
    base = base or root()
    spec_dir = spec_dir or base / config.get().research_eval_dir
    if not (spec_dir / "questions.yaml").is_file():
        print(f"no question set at {spec_dir}/questions.yaml ([tool.devkit] research_eval_dir)")
        return 1
    questions, gold = load_set(spec_dir)
    own_state = state is None  # the real state dir shares dev lit's rate buckets; a test's keeps its own
    state = state or state_dir("evals", "research")
    env, _ = lit.with_secrets(dict(os.environ))
    for d in ("client", "rates"):
        (state / d).mkdir(parents=True, exist_ok=True)
    c = Replay(
        state / "client",
        get=get,
        clock=clock,
        sleep=sleep,
        env=env,
        rate_dir=user_state_dir("lit") if own_state else state / "rates",
        cassette=state / "cassette",
        offline=offline,
    )
    arm = {
        "label": label,
        "commit": commit or git("rev-parse", "HEAD"),
        "source": source,
        "claims": n_claims,
        "auditor_model": model,
        "notes": notes_dir,
        "max_usd": max_usd,
        "seeds": SEEDS,
        "n": N,
        "held_n": HELD_N,
    }
    record: dict = {"when": time.strftime("%Y-%m-%d %H:%M", time.localtime(clock())), "arm": arm, "questions": {}}
    print(f"research eval: {len(questions)} questions, source={source}, claims={n_claims}, commit={arm['commit'][:7]}")
    print(f"{'q':<4}{'gold':>5}{'@10':>7}{'@20':>7}{'snow':>7}{'read tok':>10}{'supp':>6}{'audit tok':>10}")
    spent, skipped = 0.0, 0
    for q in questions:
        row = score_question(c, q, gold[q["id"]], source)
        row |= score_held_out(c, q["id"], gold[q["id"]], row["found@20"])
        note = Path(notes_dir) / Path(q["note"]).name if notes_dir else base / q["note"]
        text = note.read_text() if note.is_file() else ""
        audits = []
        for cl in sample_claims(text, n_claims, q["id"]) if n_claims else []:
            if spent >= max_usd:  # the auditor bills the caller's account: stop at the cap, and say so
                skipped += 1
                continue
            audits.append(audit(cl, model, runner, base))
            spent += audits[-1]["cost_usd"]
        row |= {
            "note": str(note.relative_to(base)) if note.is_relative_to(base) else str(note),
            "audits": audits,
            "supported": supported_share(audits),
            "audit_tokens": sum(a["tokens"] for a in audits),
            "audit_cost_usd": round(sum(a["cost_usd"] for a in audits), 4),
        }
        record["questions"][q["id"]] = row
        supp = "-" if row["supported"] is None else f"{row['supported']:.2f}"
        print(
            f"{q['id']:<4}{row['gold']:>5}{row['recall@10']:>7.3f}{row['recall@20']:>7.3f}{row['snowball']:>7.3f}"
            f"{row['read_tokens']:>10}{supp:>6}{row['audit_tokens']:>10}"
        )
    print(f"held-out snowball recall@{HELD_N} (half the gold as seeds) and OpenAlex calls per arm:")
    arms = [a[0] for a in HELD_ARMS]
    print(f"{'q':<4}{'seeds':>6}{'held':>5}{'search':>8}" + "".join(f"{a:>9}" for a in arms) + "  calls")
    for qid, row in record["questions"].items():
        print(
            f"{qid:<4}{row['held_seeds']:>6}{row['held']:>5}{row['held search@20']:>8.3f}"
            + "".join(f"{row[f'held@20 {a}']:>9.3f}" for a in arms)
            + "  "
            + " ".join(f"{row[f'calls {a}']}" for a in arms)
        )
    record["summary"] = summary(record["questions"])
    record["calls"] = dict(c.counts)
    record["by_source"] = c.by_source
    record["audit"] = {"cost_usd": round(spent, 4), "skipped_over_cap": skipped}
    if skipped:
        print(f"auditor stopped at ${spent:.2f} (--max-usd {max_usd}): {skipped} sampled claims not audited")
    s = record["summary"]
    print(
        f"mean recall@20 {s['recall@20']:.3f}, snowball {s['snowball']:.3f}, read tokens/question "
        f"{s['read_tokens']:.0f}, supported {'-' if s['supported'] is None else f'{s["supported"]:.2f}'} "
        f"({s['judged']} claims judged), audit tokens/question {s['audit_tokens']:.0f}"
    )
    print(
        f"HTTP: {c.counts['replayed']} replayed, {c.counts['live']} live, {c.counts['missing']} missing offline, "
        f"{c.counts['failed']} failed live, {c.counts['replayed_failures']} recorded failures replayed"
    )
    s2 = c.by_source.get("s2", {})
    print(  # whether S2 answered while the cassette was recorded: its failures replay as failures
        f"S2: {s2.get('live', 0) + s2.get('replayed', 0)} answered, "
        f"{s2.get('failed', 0) + s2.get('replayed_failures', 0)} failed, {s2.get('missing', 0)} missing offline"
    )
    path = state / f"run-{int(clock())}.json"
    path.write_text(json.dumps(record, indent=1))
    print(f"record: {path} (compare: uv run dev eval research --compare <A> {label or path.stem})")
    return 0


METRICS = (
    "recall@10",
    "recall@20",
    "snowball",
    "read_tokens",
    "supported",
    "audit_tokens",
    "held search@20",
    *(f"{k} {a[0]}" for a in HELD_ARMS for k in ("held@20", "calls")),
)


def summary(rows: dict[str, dict]) -> dict:
    """Macro means over questions; `supported` pools every judged claim (micro), since samples are a few per note."""
    vals = list(rows.values())
    out = {m: round(sum(r[m] for r in vals) / len(vals), 4) if vals else 0.0 for m in METRICS if m != "supported"}
    audits = [a for r in vals for a in r.get("audits", [])]
    out["supported"] = supported_share(audits)
    out["judged"] = sum(a["verdict"] in VERDICTS[:4] for a in audits)
    return out


def load_record(ref: str, state: Path | None = None) -> dict:
    """A research record by path, by stem (run-<epoch>), or the newest whose label is `ref`."""
    if Path(ref).is_file():
        return json.loads(Path(ref).read_text())
    d = state or state_dir("evals", "research")
    for p in sorted(d.glob("run-*.json"), key=lambda p: int(p.stem.split("-")[1]), reverse=True):
        rec = json.loads(p.read_text())
        if ref in (p.stem, rec["arm"].get("label")):
            return rec
    raise FileNotFoundError(f"no research eval record {ref!r} (a path, a run-<epoch> stem or a --label)")


def compare(a: dict, b: dict, draws: int = DRAWS, seed: int = SEED) -> dict:
    """B - A per metric over the questions both ran: the mean paired delta and its 95 % percentile bootstrap interval
    over questions (seeded). `supported` pairs only questions where both arms judged a claim."""
    qa, qb = a["questions"], b["questions"]
    ids = sorted(set(qa) & set(qb))
    rng = random.Random(seed)  # noqa: S311 - a seeded resample
    out: dict = {"paired": ids, "unpaired": sorted(set(qa) ^ set(qb)), "metrics": {}}
    for m in METRICS:
        d = [qb[i][m] - qa[i][m] for i in ids if qa[i].get(m) is not None and qb[i].get(m) is not None]
        if not d:
            out["metrics"][m] = None
            continue
        means = sorted(sum(rng.choices(d, k=len(d))) / len(d) for _ in range(draws))
        out["metrics"][m] = {
            "n": len(d),
            "delta": sum(d) / len(d),
            "lo": means[int(0.025 * draws)],
            "hi": means[min(draws - 1, int(0.975 * draws))],
        }
    return out


def render_compare(c: dict, ref_a: str, ref_b: str) -> str:
    out = [f"B {ref_b} minus A {ref_a}, over {len(c['paired'])} paired questions (95 % bootstrap over questions)"]
    for m, v in c["metrics"].items():
        if v is None:
            out.append(f"{m:<14} n/a (no paired values)")
        else:
            fmt = ".0f" if m.endswith("tokens") else ".3f"
            out.append(f"{m:<14}{v['delta']:>+10{fmt}}  [{v['lo']:+{fmt}}, {v['hi']:+{fmt}}]  n={v['n']}")
    if c["unpaired"]:
        out.append(f"unpaired (left out): {', '.join(c['unpaired'])}")
    return "\n".join(out)


def run_compare(ref_a: str, ref_b: str, state: Path | None = None) -> int:
    try:
        a, b = load_record(ref_a, state), load_record(ref_b, state)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    c = compare(a, b)
    print(render_compare(c, ref_a, ref_b))
    return 0 if c["paired"] else 1
