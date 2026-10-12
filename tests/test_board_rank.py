"""board next and board wave: rank startable items by what they open up, and pick Owns-disjoint waves.

Synthetic graphs only; no test calls GitHub.
"""

from hypothesis import given
from hypothesis import strategies as st

from devkit import board

T0 = "2026-10-11T00:00:00Z"


def mk(n, prio="P2", status="ready", blockers=(), owns=(), kind="build"):
    body = "## DONE WHEN\n- [ ] x\n" + ("## Owns\n" + "\n".join(f"- `{o}`" for o in owns) + "\n" if owns else "")
    labels = [f"type:{kind}", prio, f"status:{status}"]
    return board.Issue(n, f"devkit: {n}", labels, body, "OPEN", T0, [], board.Links(list(blockers), None))


def order(issues, done=frozenset()):
    return [i.number for i in board.startable(issues, done=set(done))]


def test_diamond_counts_each_downstream_item_once():
    """1 blocks 2 and 3, both block 4: 1 opens up 3 items, not 4 (4 is reached twice)."""
    issues = [mk(1), mk(2, status="blocked", blockers=[1]), mk(3, status="blocked", blockers=[1])]
    issues += [mk(4, status="blocked", blockers=[2, 3]), mk(5)]
    ranks = board.ranks(issues)
    assert ranks[1].downstream == [2, 3, 4]
    assert ranks[5].downstream == []
    assert order(issues) == [1, 5]


def test_long_chain_beats_a_wide_shallow_fan_and_priority_is_inherited():
    chain = [mk(1)] + [mk(n, status="blocked", blockers=[n - 1]) for n in range(2, 501)]
    chain[-1] = mk(500, prio="P0", status="blocked", blockers=[499])  # the chain's end is critical
    fan = [mk(1000, prio="P1")] + [mk(n, status="blocked", blockers=[1000]) for n in range(1001, 1004)]
    issues = chain + fan
    ranks = board.ranks(issues)
    assert len(ranks[1].downstream) == 499
    assert ranks[1].priority == "P0" and ranks[1].inherited == 500
    assert order(issues) == [1, 1000]  # P0 (inherited) before P1


def test_more_downstream_wins_within_a_priority_then_the_lower_number():
    issues = [mk(1), mk(2), mk(3), mk(4, status="blocked", blockers=[2]), mk(5, status="blocked", blockers=[4])]
    assert order(issues) == [2, 1, 3]


def test_an_own_higher_priority_is_kept_over_an_inherited_lower_one():
    issues = [mk(1, prio="P0"), mk(2, prio="P3", status="blocked", blockers=[1])]
    assert board.ranks(issues)[1].priority == "P0" and board.ranks(issues)[1].inherited is None


def test_closed_blockers_and_cycles_do_not_hang_or_count():
    issues = [mk(1, blockers=[9]), mk(2, status="blocked", blockers=[3]), mk(3, status="blocked", blockers=[2])]
    assert order(issues, done={9}) == [1]
    assert board.ranks(issues)[2].downstream == [3]


def test_epics_are_never_startable_nor_counted():
    issues = [mk(1), mk(2, prio="P0", status="blocked", blockers=[1], kind="epic")]
    assert order(issues) == [1]
    r = board.ranks(issues)[1]
    assert r.downstream == [] and r.priority == "P0"  # an epic waiting on it still lends its priority


def test_reason_names_inheritance_and_what_it_opens():
    issues = [mk(1), mk(2, prio="P0", status="blocked", blockers=[1]), mk(3, status="blocked", blockers=[2])]
    why = board.reason(board.ranks(issues)[1])
    assert "P0 from #2" in why and "unblocks 2" in why and "#2" in why


def test_next_prints_a_reason_per_line(monkeypatch):
    issues = [mk(1), mk(2, status="blocked", blockers=[1]), mk(3)]
    monkeypatch.setattr(board, "fetch", lambda *a, **k: issues)
    monkeypatch.setattr(board, "resolved", lambda issues: set())
    lines = board.next_items(None, 5).splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("#1") and "unblocks 1" in lines[0]
    assert "unblocks 0" in lines[1]


def test_wave_picks_owns_disjoint_items_and_avoids_in_progress_owns():
    issues = [
        mk(1, owns=["devkit/board.py"]),
        mk(2, owns=["devkit/"]),  # overlaps #1 (a prefix of it)
        mk(3, owns=["docs/specs/*.md"]),  # overlaps #9, in progress
        mk(4, owns=["tests/test_x.py"]),
        mk(5),  # no Owns: overlaps nothing
        mk(9, status="in-progress", owns=["docs/specs/multi-agent.md"]),
    ]
    assert [i.number for i in board.wave(issues, 10, set())] == [1, 4, 5]
    assert [i.number for i in board.wave(issues, 2, set())] == [1, 4]


def test_owns_split_on_commas_inside_one_backtick():
    """Issues filed with `--owns a,b` hold one backticked entry."""
    i = board.Issue(1, "x: y", [], "## Owns\n- `devkit/board.py,tests/test_board.py`\n", "OPEN", T0)
    assert board.owns_of(i) == ["devkit/board.py", "tests/test_board.py"]


def test_wave_command(monkeypatch):
    issues = [mk(1, owns=["a"]), mk(2, owns=["a/b"]), mk(3, owns=["c"])]
    monkeypatch.setattr(board, "fetch", lambda *a, **k: issues)
    monkeypatch.setattr(board, "resolved", lambda issues: set())
    out = board.wave_items(3)
    assert [ln.split()[0] for ln in out.splitlines()] == ["#1", "#3"]
    monkeypatch.setattr(board, "fetch", lambda *a, **k: [])
    assert "nothing startable" in board.wave_items(3)


@st.composite
def dags(draw):
    n = draw(st.integers(1, 12))
    issues = []
    for k in range(1, n + 1):
        blockers = draw(st.lists(st.integers(1, k - 1), unique=True, max_size=3)) if k > 1 else []
        prio = draw(st.sampled_from(board.PRIORITIES))
        issues.append(mk(k, prio, "blocked" if blockers else "ready", blockers))
    return issues


@given(dags())
def test_downstream_is_the_transitive_closure_and_priority_the_min_over_it(issues):
    by = {i.number: i for i in issues}
    for n, r in board.ranks(issues).items():
        reach, todo = set(), [n]
        while todo:
            x = todo.pop()
            for j in issues:
                if x in j.blockers and j.number not in reach:
                    reach.add(j.number)
                    todo.append(j.number)
        assert r.downstream == sorted(reach)
        assert r.priority == min([by[n].priority, *(by[j].priority for j in reach)])


@given(dags(), st.integers(1, 6))
def test_wave_without_owns_is_the_top_n_of_next(issues, n):
    ranked = [x.number for x in board.startable(issues, done=set())]
    assert [i.number for i in board.wave(issues, n, set())] == ranked[:n]
