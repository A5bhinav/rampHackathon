"""Spec 0046's measurement (`lab redteam-bench`): a planted break is checked before it is used, the
bench runs the rule breaks.json pins and never the working tree's select.py, it refuses to run unless
the breaks were committed before the score, and "found", the pass bar and the guard are computed
exactly as the spec fixes them. Every test here builds its own temporary git repository or calls the
bench's pieces directly, so it runs in CI's shallow checkout, at budgets far below the measurement's."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import hypothesis
import pytest
from dialback_evals import redteam
from dialback_evals.cli import main
from dialback_evals.gencorpus import _dump, _load, checked
from dialback_evals.redteam import Refused, load_rule
from dialback_evals.worldgen import NumberSpec, make_world

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PINNED = (FIXTURES / "select-e3d57b5.py.txt").read_text(encoding="utf-8")   # select.py as breaks.json pins it
LINES = PINNED.split("\n")
REAL = "+14155550142"
NUM = "+13035550169"

FAX_CHECK = 'if any(str(s.role).strip().lower() == "fax" for s, _, _ in visible):'
SOURCES_CHECK = "if len(sources) < settings.min_source_types:"
LOOKALIKES = "look_ids = {d.id for d in docs.values() if (sd := sender_domain(d.sender)) and is_lookalike(sd, domains)}"
FAX_WORLD = make_world(numbers=[NumberSpec(NUM, ["contract", "listing"], 400, role="fax")])
LOOKALIKE_WORLD = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 500),
                                      NumberSpec(NUM, ["email"], 100, sender="lookalike")])


def _break(bid: str, kind: str, old: str, new: str, witness) -> dict:
    """A one-line break of the pinned select.py, in breaks.json's shape."""
    [n] = [i + 1 for i, line in enumerate(LINES) if line.strip() == old]
    indent = LINES[n - 1][:len(LINES[n - 1]) - len(LINES[n - 1].lstrip())]
    return {"id": bid, "kind": kind, "line": n, "old": LINES[n - 1], "new": indent + new, "why": "a test break",
            "witness": _dump(witness)}


NO_FAX = _break("T-fax", "defense", FAX_CHECK, "if False:", FAX_WORLD)
NO_LOOKALIKES = _break("T-look", "helps", LOOKALIKES, "look_ids = set()", LOOKALIKE_WORLD)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=lab", "-c", "user.email=lab@example.invalid",
                           "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    return repo


def _bench_repo(tmp_path: Path, breaks: list[dict], *, working_tree_rule: str) -> Path:
    """A repository where select.py is pinned at one commit, then breaks.json, then the score, in that
    order, and the working tree's select.py is `working_tree_rule`."""
    repo = _repo(tmp_path)
    rule = _commit(repo, {redteam.SELECT: PINNED}, "the rule")
    spec = {"select_commit": rule, "select_sha256": hashlib.sha256(PINNED.encode()).hexdigest(), "breaks": breaks}
    _commit(repo, {redteam.BREAKS: json.dumps(spec, indent=1)}, "the breaks")
    _commit(repo, {redteam.GUIDED: "# the score\n"}, "the score")
    (repo / redteam.SELECT).write_text(working_tree_rule, encoding="utf-8")
    return repo


# ---- 10: an invalid break is refused, by id --------------------------------------------------------

def test_an_invalid_break_is_refused():
    real, broken = redteam.validate({"breaks": [NO_FAX, NO_LOOKALIKES]}, PINNED)
    assert set(broken) == {"T-fax", "T-look"} and real.RULE_VERSION == "ledger-select-v1"
    assert broken["T-fax"].select(**FAX_WORLD.kwargs) != real.select(**FAX_WORLD.kwargs)
    invalid = [
        {**NO_FAX, "id": "T-line", "line": NO_FAX["line"] + 1},                      # line number does not match
        {**NO_FAX, "id": "T-text", "old": NO_FAX["old"] + " "},                      # text does not match
        {**NO_FAX, "id": "T-syntax", "new": NO_FAX["new"].rstrip(":")},              # does not compile
        {**NO_FAX, "id": "T-same", "witness": _dump(LOOKALIKE_WORLD)},              # its witness shows no difference
        {**NO_FAX, "id": "T-kind", "kind": "other"},                                 # not a kind the bench scores
    ]
    for bad in invalid:
        with pytest.raises(Refused, match=bad["id"]):
            redteam.validate({"breaks": [NO_FAX, bad]}, PINNED)


# ---- 11: the pinned rule, and the commit order -----------------------------------------------------

def test_bench_runs_the_pinned_rule(tmp_path):
    repo = _repo(tmp_path)
    pinned = PINNED + '\nBENCH_MARK = "pinned"\n'
    working = PINNED + '\nBENCH_MARK = "working tree"\n'
    commit = _commit(repo, {redteam.SELECT: pinned}, "the rule")
    (repo / redteam.SELECT).write_text(working, encoding="utf-8")         # a later, uncommitted edit
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    source = redteam.pinned_source({"select_commit": commit, "select_sha256": sha(pinned)}, repo)
    assert load_rule(source, "t").BENCH_MARK == "pinned"
    with pytest.raises(Refused, match="sha256"):                          # the commit holds something else
        redteam.pinned_source({"select_commit": commit, "select_sha256": sha(working)}, repo)
    absent = "0" * 40                                                      # a shallow checkout lacks the commit:
    assert redteam.pinned_source({"select_commit": absent, "select_sha256": sha(working)}, repo) == working
    with pytest.raises(Refused, match="not available"):                   # only a byte-identical file stands in
        redteam.pinned_source({"select_commit": absent, "select_sha256": sha(pinned)}, repo)


def test_commit_order_is_checked(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {redteam.BREAKS: "{}"}, "the breaks")
    with pytest.raises(Refused, match="guided.py"):                       # no score yet: nothing to order against
        redteam.check_commit_order(repo)
    _commit(repo, {redteam.GUIDED: "# the score\n"}, "the score")
    redteam.check_commit_order(repo)                                       # breaks, then the score: it runs
    (repo / redteam.BREAKS).write_text('{"edited": true}', encoding="utf-8")
    with pytest.raises(Refused, match="uncommitted"):
        redteam.check_commit_order(repo)
    _commit(repo, {redteam.BREAKS: '{"edited": true}'}, "the breaks, edited after the score")
    with pytest.raises(Refused, match="breaks.json changed"):
        redteam.check_commit_order(repo)
    together = _repo(tmp_path / "together")
    _commit(together, {redteam.BREAKS: "{}", redteam.GUIDED: "# the score\n"}, "both at once")
    with pytest.raises(Refused, match="breaks.json changed"):
        redteam.check_commit_order(together)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{together}", str(shallow)], check=True)
    with pytest.raises(Refused, match="full history"):
        redteam.check_commit_order(shallow)


# ---- 12: found, the bar, the held-out flag, the report ---------------------------------------------

def test_found_means_a_label_the_real_rule_lacks():
    assert redteam.first_found([[], ["A"], ["A", "B"]], [[], ["A"], ["A"]]) == 2
    assert redteam.first_found([["A"], ["A"]], [["A"], ["A", "C"]]) is None   # a label the real rule shows too
    same = {**NO_FAX, "id": "T-none", "new": NO_FAX["old"]}               # the pinned rule standing in for a break
    run = redteam.run_arm(PINNED, same, seed=0, arm="archive", budget=20, new_moves=("incontext",))
    assert run["worlds_with_labels"] > 0 and run["found_at"] is None    # labels shown, but none the real rule lacks
    loose = _break("T-sources", "defense", SOURCES_CHECK, "if False:", FAX_WORLD)
    run = redteam.run_arm(PINNED, loose, seed=0, arm="archive", budget=20, new_moves=("incontext",))
    assert run["found_at"] is not None and run["found_labels"]
    real = load_rule(PINNED, "real")
    assert not set(run["found_labels"]) & set(checked(_load(run["found_world"]), real))


def test_sign_test_bar():
    assert redteam.sign_test(9, 1) == pytest.approx(11 / 1024)
    assert redteam.sign_test(6, 0) == pytest.approx(1 / 64)
    assert redteam.bar([1] * 9 + [-1])["passed"]                            # 9 of 10: p = 0.011
    assert not redteam.bar([1] * 8 + [-1] * 2)["passed"]                    # 8 of 10: p = 0.055
    assert not redteam.bar([-1] * 9 + [1])["passed"]                        # one-sided: losing never passes
    six = redteam.bar([2, 1, 1, 3, 1, 1, 0, 0, 0, 0])
    assert six["passed"] and six["differing"] == 6 and six["p"] == pytest.approx(1 / 64)
    five = redteam.bar([1] * 5 + [0] * 5)
    assert not five["passed"] and five["differing"] == 5                    # under 6 differing: no evidence, a fail
    assert not redteam.bar([0] * 10)["passed"]
    assert redteam.guard([1, -1, 0])["passed"] and not redteam.guard([1, -1, -1])["passed"]
    both = {"guided": True, "guided-new": True}
    assert redteam.keep(both, both, {"guided": 5, "guided-new": 5}) == "guided-new"   # a tie keeps guided-new
    assert redteam.keep(both, both, {"guided": 6, "guided-new": 5}) == "guided"
    assert redteam.keep(both, {"guided": True, "guided-new": False}, {"guided": 1, "guided-new": 9}) == "guided"
    assert redteam.keep({"guided": False, "guided-new": False}, both, {"guided": 1, "guided-new": 1}) is None


def test_held_out_needs_the_flag(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("dialback_evals.cli.bench", lambda seeds, **k: calls.append((seeds, k["held_out"])) or {})
    out = str(tmp_path / "bench.json")
    assert main(["redteam-bench", "--seeds", "1000-1009", "--out", out]) == 2
    assert main(["redteam-bench", "--seeds", "3,1004", "--out", out]) == 2
    assert calls == [] and not Path(out).exists()
    assert main(["redteam-bench", "--seeds", "0-9", "--out", out]) == 0
    assert main(["redteam-bench", "--seeds", "1000-1009", "--held-out", "--out", out]) == 0
    assert calls == [(list(range(10)), False), (list(range(1000, 1010)), True)]
    with pytest.raises(Refused, match="held-out"):                        # the bench itself refuses too
        redteam.bench([1000], held_out=False, out=tmp_path / "x.json", repo=tmp_path)


def test_bench_writes_its_report(tmp_path):
    # The working tree's select.py does not even load: the bench never reads it.
    repo = _bench_repo(tmp_path, [NO_FAX, NO_LOOKALIKES], working_tree_rule="raise RuntimeError('not this one')\n")
    out = tmp_path / "out" / "redteam-bench.json"
    report = redteam.bench([0, 1], held_out=False, out=out, repo=repo, jobs=1, budget=6)
    assert json.loads(out.read_text(encoding="utf-8")) == report
    assert report["hypothesis"] == hypothesis.__version__ and report["budget"] == 6 and report["seeds"] == [0, 1]
    for seed in ("0", "1"):
        for arm in redteam.ARMS:
            row = report["per_seed"][seed][arm]
            assert set(row) >= {"helps", "defense", "found", "worlds", "calls"} and row["calls"] == 6
    for arm in ("guided", "guided-new"):
        assert set(report["sign_tests"][arm]) >= {"wins", "losses", "differing", "p", "passed"}
        assert set(report["guards"][arm]) >= {"wins", "losses", "passed"}
        assert len(report["primary"][arm]["per_seed"]) == 2
    assert set(report["first_found"]) == {"T-fax", "T-look"}
    assert set(report["never_found"]) <= {"T-fax", "T-look"}
    assert report["kept"] in (None, "guided", "guided-new")
    share = report["gain_share_on_pinned_rule"]
    assert share["worlds"] > 0 and 0 <= share["gain"] <= 1 and 0 <= share["gain_new"] <= 1
    bad = _bench_repo(tmp_path / "bad", [NO_FAX, {**NO_LOOKALIKES, "line": NO_LOOKALIKES["line"] + 1}],
                      working_tree_rule=PINNED)
    with pytest.raises(Refused, match="T-look"):
        redteam.bench([0], held_out=False, out=tmp_path / "bad.json", repo=bad, jobs=1, budget=6)
