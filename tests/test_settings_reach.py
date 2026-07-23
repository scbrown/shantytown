"""`role set` must say who the settings rewrite did NOT reach (internal-ref item 2,
recovered from a stash on internal-ref).

THE INCIDENT THESE TESTS ENCODE. `--settings` is read ONCE, at launch. So the
operator who just changed the hooks has, at that moment, changed nothing about the
live fleet — and the command told them the opposite: it printed the paths it wrote
and exited 0, which reads as done. Twice that was invisible:

  * a Stop-hook FIX was emitted and two live agents kept the broken hook, staying
    DEAF for the next hour;
  * a PreToolUse guard that hard-blocks every edit was emitted and the fleet stayed
    green for half an hour — not because the guard was safe, but because nobody had
    relaunched into it. The first agent to restart, for an unrelated reason, found
    it with its body.

`st crew` could answer the same question, but only if you thought to ask, and nobody
in that incident had a reason to. This report does not require suspicion.

AND THE ANTI-DIVERGENCE INVARIANT. `crew` reports this when asked; `role set`
reports it when it CAUSES it. If those two ever disagree, the first symptom is one
surface calling an agent healthy while the other calls it stale — which is the exact
ambiguity nipg exists to remove. test_crew_and_role_set_cannot_disagree pins them.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

import shantytown.cli as cli
from shantytown.cli import main, OK, _reach_buckets, _settings_verdict
from shantytown.launched import CURRENT, STALE, UNKNOWN


class FakePanes:
    """Panes where liveness is a set of pane names. `exists` is the only method
    the reach path uses."""

    def __init__(self, live: set[str]):
        self.live = live

    def exists(self, pane) -> bool:
        return pane in self.live

    def capture(self, pane, history: int = 0, attrs: bool = False) -> str:
        return ""

    def send(self, pane, text) -> None:
        pass


def _crew(root: Path, **agents) -> Path:
    d = root / "crew"
    d.mkdir(parents=True, exist_ok=True)
    for n, spec in agents.items():
        (d / f"{n}.json").write_text(json.dumps(spec))
    return root


def _stamp(root: Path, agent: str, role: str = "worker"):
    """Stamp `agent` as launched on the CURRENT bytes of its role settings file,
    through the real FilesLaunches API.

    Deliberately not hand-written JSON: the first version of this helper invented
    the field names (`settings_path` instead of `settings`) and every stamp it
    wrote read back as UNKNOWN, so three tests passed for the wrong reason and one
    failed for a reason that had nothing to do with the code under test. A fixture
    that fakes a format can drift from it; one that calls the API cannot.

    STALE is then produced the way it happens in life — the file changes underneath
    a stamped agent, which is exactly what `role set` does when it re-emits.
    """
    from shantytown.launched import FilesLaunches
    FilesLaunches(root / "launched").record(
        agent, root / "settings" / f"{role}.settings.json")


def _settings(root: Path, role: str, body: str = '{"hooks": {}}'):
    d = root / "settings"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{role}.settings.json").write_text(body)


# --- the pure rules, tested directly -----------------------------------------

def test_verdict_is_not_live_for_a_down_agent():
    """A down agent has no loaded settings to be stale. Calling it `current` OR
    `stale` would both be claims we did not measure."""
    class L:
        def verdict(self, name): raise AssertionError("must not probe a down agent")
    assert _settings_verdict(L(), "ellie", live=False) == cli.NOT_LIVE


def test_buckets_split_stale_from_unknown_and_drop_the_rest():
    got = _reach_buckets([("a", STALE), ("b", CURRENT), ("c", UNKNOWN),
                          ("d", cli.NOT_LIVE)])
    assert got == (["a"], ["c"])



def _reported(out: str) -> tuple[set[str], set[str]]:
    """(stale, unknown) as NAMED BY THE REPORT — parsed, not substring-matched.

    The first version of these assertions did `"down" not in out`, and passed/failed
    on the pytest tmp_path (".../test_a_down_agent_is_never_lis0/...") rather than on
    anything the report said. A test that can match its own scaffolding is not
    testing the output.
    """
    stale, unknown, mode = set(), set(), None
    for line in out.splitlines():
        t = line.strip()
        if "NOT DEPLOYED to" in t:
            mode = "stale"
            stale |= {x.strip() for x in t.split(":", 1)[1].split(",") if x.strip()}
        elif "have no launch stamp" in t:
            mode = "unknown"
        elif mode == "unknown" and t and not t.startswith(("They", "Treat", "?", "⚠")):
            unknown |= {x.strip() for x in t.split(",") if x.strip()}
            mode = None
    return stale, unknown


# --- role set: the report ------------------------------------------------------

def _fixture(tmp_path, live):
    root = _crew(tmp_path,
                 sattler={"role": "administrator", "pane": "p-sattler"},
                 ellie={"role": "worker", "reports_to": "sattler", "pane": "p-ellie"},
                 ian={"role": "worker", "reports_to": "sattler", "pane": "p-ian"},
                 down={"role": "worker", "reports_to": "sattler", "pane": "p-down"})
    _settings(root, "worker")
    # ellie + down are stamped against these placeholder bytes. `role set` will
    # re-emit worker.settings.json with the REAL settings content, so the file
    # changes underneath them and both become STALE — which is precisely the
    # incident: the rewrite is what made them stale.
    _stamp(root, "ellie")
    _stamp(root, "down")
    # ian has no stamp -> UNKNOWN.
    return root


def test_role_set_names_the_live_agents_the_rewrite_did_not_reach(tmp_path, monkeypatch, capsys):
    """THE POINT OF THE WHOLE CHANGE. The mutation succeeded and was printed; the
    report then says, unprompted, that nothing live actually picked it up."""
    root = _fixture(tmp_path, live=None)
    monkeypatch.setattr(cli, "Tmux",
                        lambda *a, **k: FakePanes({"p-sattler", "p-ellie", "p-ian"}))
    rc = main(["--root", str(root), "role", "set", "ellie", "worker"])
    assert rc == OK                      # the report NEVER turns a done mutation into a failure
    out = capsys.readouterr().out
    assert "NOT DEPLOYED" in out
    stale, unknown = _reported(out)
    assert "ellie" in stale              # stamped, then the rewrite changed the file
    assert "ian" in unknown              # no stamp -> UNKNOWN, reported separately
    assert "st stop" in out              # and it says how to actually deploy it


def test_a_down_agent_is_never_listed(tmp_path, monkeypatch, capsys):
    """A down agent reads the current file when it next starts. Listing it is noise
    that buries the agents actually running old hooks."""
    root = _fixture(tmp_path, live=None)
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: FakePanes({"p-ellie"}))
    main(["--root", str(root), "role", "set", "ellie", "worker"])
    stale, unknown = _reported(capsys.readouterr().out)
    assert "ellie" in stale
    assert "down" not in (stale | unknown), (
        "a down agent was reported as not-reached; it has no loaded settings to be "
        "stale and will read the current file when it next starts"
    )


def test_only_roles_this_rewrite_TOUCHED_are_reported(tmp_path, monkeypatch, capsys):
    """`role set ellie worker` emits worker.settings.json and nothing else. A stale
    ADMINISTRATOR is genuinely stale but was not missed by THIS rewrite — and this
    report claims, by its name, to say who the rewrite did not reach. Over-claiming
    there is the same defect the change exists to fix, one level down."""
    root = _fixture(tmp_path, live=None)
    _settings(root, "administrator")
    _stamp(root, "sattler", role="administrator")
    # sattler must be GENUINELY STALE, or this test proves nothing: with an
    # unchanged administrator file sattler reads CURRENT and is excluded whether
    # the role filter exists or not. (First version of this test did exactly that
    # — deleting the filter left it green. Caught by running the control.)
    _settings(root, "administrator", body='{"hooks": {"changed": true}}')
    monkeypatch.setattr(cli, "Tmux",
                        lambda *a, **k: FakePanes({"p-sattler", "p-ellie", "p-ian"}))
    main(["--root", str(root), "role", "set", "ellie", "worker"])
    stale, unknown = _reported(capsys.readouterr().out)
    assert "ellie" in stale
    assert "sattler" not in (stale | unknown), (
        "reported an administrator as not-reached by a rewrite that never touched "
        "its settings file"
    )


def test_silent_when_every_live_agent_is_current(tmp_path, monkeypatch, capsys):
    """No noise on the happy path — otherwise the warning stops being read."""
    root = _crew(tmp_path, sattler={"role": "administrator", "pane": "p-s"},
                 ellie={"role": "worker", "reports_to": "sattler", "pane": "p-e"})
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: FakePanes({"p-s", "p-e"}))
    # Emit the real settings FIRST, then stamp ellie on them, so the re-emit below
    # is byte-identical and ellie stays CURRENT.
    main(["--root", str(root), "role", "set", "ellie", "worker"])
    capsys.readouterr()
    _stamp(root, "ellie")
    main(["--root", str(root), "role", "set", "ellie", "worker"])
    out = capsys.readouterr().out
    assert "NOT DEPLOYED" not in out


def test_report_is_never_fatal_when_it_cannot_look(tmp_path, monkeypatch, capsys):
    """It reports on a mutation that has ALREADY succeeded and been printed. A
    report that turned a completed `role set` into a failure would be a worse bug
    than the one it warns about."""
    root = _fixture(tmp_path, live=None)

    class Exploding:
        def exists(self, pane): raise OSError("tmux is gone")

    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: Exploding())
    try:
        rc = main(["--root", str(root), "role", "set", "ellie", "worker"])
    except Exception as e:                                    # pragma: no cover
        pytest.fail(f"the report raised out of a successful role set: {e!r}")
    assert rc == OK
    # and the card was still written — the mutation is not rolled back
    assert json.loads((root / "crew" / "ellie.json").read_text())["role"] == "worker"


# --- THE ANTI-DIVERGENCE INVARIANT --------------------------------------------

def test_crew_and_role_set_cannot_disagree(tmp_path, monkeypatch, capsys):
    """`crew` reports this when ASKED; `role set` reports it when it CAUSES it.
    The two must never call the same agent healthy and stale.

    They share `_settings_verdict` and `_reach_buckets`, so this asserts the thing
    that sharing buys. Before this change the rule was written twice — once in a
    helper and once inline in `_cmd_crew` — which is precisely the divergence the
    helper's docstring warned about.
    """
    root = _fixture(tmp_path, live=None)
    live = {"p-sattler", "p-ellie", "p-ian"}
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: FakePanes(live))

    main(["--root", str(root), "crew"])
    crew_out = capsys.readouterr().out
    # crew renders a verdict per row; collect who it calls STALE / unknown
    crew_stale = {ln.split()[0] for ln in crew_out.splitlines() if f" {STALE} " in ln}
    crew_unknown = {ln.split()[0] for ln in crew_out.splitlines() if " unknown " in ln}

    from shantytown.cli import _settings_reach
    from shantytown.files import FilesRegistry

    class A:
        pass
    a = A(); a.root = root
    agents = FilesRegistry(root / "crew").all()
    rs_stale, rs_unknown = _settings_reach(a, FakePanes(live), agents)

    assert set(rs_stale) == crew_stale, (
        f"role set says stale={sorted(rs_stale)}, crew's column says "
        f"{sorted(crew_stale)} — the two surfaces disagree"
    )
    assert set(rs_unknown) == crew_unknown, (
        f"role set says unknown={sorted(rs_unknown)}, crew's column says "
        f"{sorted(crew_unknown)}"
    )
    # and the invariant is only meaningful if there was something to agree ABOUT
    assert crew_stale or crew_unknown, "fixture produced no stale/unknown agents"
