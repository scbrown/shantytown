"""st init — the scaffold wizard.

What it has to get right, and why each one is load-bearing:

  IT WRITES THROUGH THE EXISTING SEAMS. Cards via the registry (which is where a
  card gets its generated pane), roles and stop-hook routing via tier.role_set,
  hook files via the emitter `roles set` uses. A wizard with its own card writer
  would be a second way to declare a crew, and the first thing it would drift on
  is the field this command exists to stop people hand-editing.

  IT NEVER OVERWRITES. An existing store is a refusal, an existing card under
  --force is kept untouched, an existing config is kept. A scaffolder that can
  destroy a live deployment on a mistyped --root is a footgun.

  IT NEVER BLOCKS ON A PROMPT. No terminal and no --yes is a REFUSAL, not an
  input() that hangs inside a script or a hook.

  WHAT IT PRODUCES IS RUNNABLE. The generated config must parse, and the cards
  must be startable — an init that ends in `st start` refusing has failed at the
  one thing it is for.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shantytown import cli, config, scaffold


class _Ask:
    """A scripted asker: returns answers in order and records the prompts.

    `notes` is a SEPARATE list, mirroring the separate channel the wizard uses to
    report a rejected answer — routing that through ask() would consume the
    operator's next line, so a test that let them share would not notice.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.notes: list[str] = []

    def __call__(self, prompt, default=""):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def note(self, msg):
        self.notes.append(msg)


class _Args:
    def __init__(self, root, **kw):
        self.root = Path(root)
        self.admin = kw.get("admin")
        self.crew = kw.get("crew")
        self.workspaces = kw.get("workspaces")
        self.mode = kw.get("mode")
        self.hibernate = kw.get("hibernate", False)
        self.yes = kw.get("yes", False)
        self.force = kw.get("force", False)
        self.dry_run = kw.get("dry_run", False)
        self.registry = "files"; self.backend = None; self.repo = None


def _card(root, name) -> dict:
    return json.loads((Path(root) / "crew" / f"{name}.json").read_text())


# --- name validation --------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "  ", "Sattler", "sat tler", "sat/tler",
                                 "-sattler", "sat.tler", "sat:tler"])
def test_unusable_names_are_refused(bad):
    """A name becomes BOTH a filename and a tmux session name; `.` and `:` are
    tmux address syntax. Validate against the intersection up front, not at
    launch."""
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.validate_name(bad)


@pytest.mark.parametrize("ok", ["sattler", "crew-1", "a_b", "x9"])
def test_usable_names_pass(ok):
    assert scaffold.validate_name(ok) == ok


def test_a_duplicate_name_is_refused():
    with pytest.raises(scaffold.ScaffoldError) as e:
        scaffold.make_answers(admin="a", workers=("b", "b"))
    assert "twice" in str(e.value)


def test_the_admin_cannot_also_be_a_worker():
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.make_answers(admin="a", workers=("a",))


def test_a_mode_that_does_not_exist_yet_is_refused():
    """init can only write a config naming a BUILT-IN mode — a custom one has
    nowhere to be defined yet, and `st start` would refuse right after init said
    everything was ready."""
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.make_answers(admin="a", mode="night")


# --- the question script ----------------------------------------------------

def test_the_wizard_collects_every_answer():
    ask = _Ask("sattler", "arnold, billy", "/srv/crew", "heavy", "yes", "45")
    got = scaffold.ask_all(ask)
    assert got.admin == "sattler"
    assert got.workers == ("arnold", "billy"), "comma AND space separated"
    assert got.workspaces == "/srv/crew"
    assert got.mode == "heavy"
    assert got.hibernate is True and got.max_quiet_minutes == 45


def test_enter_accepts_every_default():
    ask = _Ask("", "", "", "", "")
    got = scaffold.ask_all(ask)
    assert got.admin == scaffold.DEFAULT_ADMIN
    assert got.workers == () and got.workspaces is None
    assert got.mode == config.DEFAULT_MODE and got.hibernate is False


def test_the_quiet_bound_is_not_asked_when_hibernate_is_declined():
    """Asking about a bound on a policy that will never be consulted is a question
    that costs the operator attention and buys nothing."""
    ask = _Ask("sattler", "", "", "lite", "no")
    got = scaffold.ask_all(ask)
    assert got.hibernate is False
    assert not any("minutes of quiet" in p for p in ask.prompts)


def test_saying_yes_asks_for_the_quiet_bound():
    ask = _Ask("sattler", "", "", "lite", "yes", "20")
    got = scaffold.ask_all(ask)
    assert got.hibernate is True and got.max_quiet_minutes == 20


def test_a_bad_answer_is_RE_ASKED_not_fatal():
    """A wizard that aborts on one typo makes the operator re-answer the questions
    they already got right."""
    ask = _Ask("Bad Name", "sattler", "", "", "lite", "off")
    got = scaffold.ask_all(ask, note=ask.note)
    assert got.admin == "sattler"
    assert any("not usable" in n for n in ask.notes), "it must say why"


def test_a_rejection_NOTE_never_consumes_an_answer():
    """The rejection goes out on `note`, not `ask`. Through `ask` it would call
    input() again and eat the operator's next line as the answer to a question
    nobody asked — and every later answer would land on the wrong question."""
    ask = _Ask("Bad Name", "sattler", "arnold", "", "heavy", "off")
    got = scaffold.ask_all(ask, note=ask.note)
    assert got.admin == "sattler"
    assert got.workers == ("arnold",), "the answers must not have shifted by one"
    assert got.mode == "heavy"


def test_defaults_from_flags_are_offered_as_the_defaults():
    d = scaffold.Answers(admin="sattler", workers=("arnold",), mode="heavy")
    ask = _Ask("", "", "", "", "")
    got = scaffold.ask_all(ask, defaults=d)
    assert got.admin == "sattler" and got.workers == ("arnold",)
    assert got.mode == "heavy"


# --- what it writes ---------------------------------------------------------

def test_it_creates_a_runnable_store(tmp_path, capsys):
    root = tmp_path / ".shanty"
    rc = cli._cmd_init(_Args(root, yes=True, admin="sattler", crew="arnold,billy"))
    assert rc == cli.OK

    for d in scaffold.DIRS:
        assert (root / d).is_dir(), f"{d}/ was not created"
    assert _card(root, "sattler")["role"] == "administrator"
    assert _card(root, "arnold")["reports_to"] == "sattler"
    assert (root / "settings" / "administrator.settings.json").is_file()
    assert (root / "settings" / "worker.settings.json").is_file()
    assert config.config_path(root).is_file()


def test_every_card_it_writes_has_a_GENERATED_pane(tmp_path):
    """The gap this command closes: a card with no pane names no session, and
    launch/attach/stop/tend all resolve an agent THROUGH its pane."""
    root = tmp_path / ".shanty"
    cli._cmd_init(_Args(root, yes=True, admin="sattler", crew="arnold"))
    assert _card(root, "sattler")["pane"] == "st-sattler"
    assert _card(root, "arnold")["pane"] == "st-arnold"


def test_the_config_it_writes_PARSES(tmp_path):
    """A config this command writes but `st start` would refuse is the worst
    possible handoff."""
    root = tmp_path / ".shanty"
    cli._cmd_init(_Args(root, yes=True, admin="sattler", mode="heavy",
                        hibernate=True))
    cfg = config.load(root)
    assert cfg.mode == "heavy"
    assert cfg.hibernate.enabled is True


def test_workspaces_become_one_directory_per_agent(tmp_path):
    root = tmp_path / ".shanty"
    cli._cmd_init(_Args(root, yes=True, admin="sattler", crew="arnold",
                        workspaces="/srv/crew/"))
    assert _card(root, "arnold")["workspace"] == "/srv/crew/arnold"
    assert _card(root, "sattler")["workspace"] == "/srv/crew/sattler"


def test_no_workspaces_leaves_the_field_unset(tmp_path):
    root = tmp_path / ".shanty"
    cli._cmd_init(_Args(root, yes=True, admin="sattler"))
    assert "workspace" not in _card(root, "sattler")


def test_the_admin_gets_no_reports_to(tmp_path):
    root = tmp_path / ".shanty"
    cli._cmd_init(_Args(root, yes=True, admin="sattler", crew="arnold"))
    assert _card(root, "sattler")["reports_to"] is None


# --- refusals ---------------------------------------------------------------

def test_an_existing_store_is_refused(tmp_path, capsys):
    """A second init over a live deployment is far more likely to be a mistyped
    --root than an intention."""
    root = tmp_path / ".shanty"
    cli._cmd_init(_Args(root, yes=True, admin="sattler"))
    rc = cli._cmd_init(_Args(root, yes=True, admin="someone-else"))
    assert rc == cli.REFUSED
    assert not (root / "crew" / "someone-else.json").exists()
    err = capsys.readouterr().err
    assert "already a deployment" in err and "--force" in err


def test_force_keeps_every_existing_card_untouched(tmp_path):
    root = tmp_path / ".shanty"
    cli._cmd_init(_Args(root, yes=True, admin="sattler", crew="arnold"))
    (root / "crew" / "arnold.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "sattler", "pane": "shanty-arnold",
         "workspace": "/keep/me", "retired": False}))

    rc = cli._cmd_init(_Args(root, yes=True, admin="sattler", crew="arnold,billy",
                             force=True))
    assert rc == cli.OK
    kept = _card(root, "arnold")
    assert kept["pane"] == "shanty-arnold", "an existing pane must survive"
    assert kept["workspace"] == "/keep/me"
    assert _card(root, "billy")["pane"] == "st-billy", "the new one is still made"


def test_force_does_not_overwrite_an_existing_config(tmp_path):
    root = tmp_path / ".shanty"
    cli._cmd_init(_Args(root, yes=True, admin="sattler"))
    config.config_path(root).write_text('[startup]\nmode = "heavy"\n')
    cli._cmd_init(_Args(root, yes=True, admin="sattler", force=True))
    assert config.load(root).mode == "heavy", "the operator's config must survive"


def test_no_terminal_and_no_yes_REFUSES_rather_than_blocking(tmp_path, capsys):
    """A wizard that blocks forever inside a script or a hook is worse than one
    that says it cannot ask."""
    root = tmp_path / ".shanty"
    rc = cli._cmd_init(_Args(root), isatty=lambda: False)
    assert rc == cli.REFUSED
    assert not (root / "crew").exists(), "a refusal must create nothing"
    assert "--yes" in capsys.readouterr().err


def test_a_bad_name_from_a_flag_is_refused_before_anything_is_written(tmp_path, capsys):
    root = tmp_path / ".shanty"
    rc = cli._cmd_init(_Args(root, yes=True, admin="Bad Name"))
    assert rc == cli.REFUSED
    assert not (root / "crew").exists()
    assert "not usable" in capsys.readouterr().err


def test_dry_run_writes_nothing(tmp_path, capsys):
    root = tmp_path / ".shanty"
    rc = cli._cmd_init(_Args(root, yes=True, admin="sattler", crew="arnold",
                             dry_run=True))
    assert rc == cli.OK
    assert not (root / "crew").exists() and not config.config_path(root).exists()
    out = capsys.readouterr().out
    assert "nothing written" in out
    assert "st-sattler" in out, "the plan must show the panes it WOULD generate"


def test_declining_the_confirmation_writes_nothing(tmp_path, capsys):
    root = tmp_path / ".shanty"
    rc = cli._cmd_init(_Args(root), ask=_Ask("sattler", "", "", "", "", "no"),
                       isatty=lambda: True)
    assert rc == cli.REFUSED
    assert not (root / "crew").exists()
    assert "nothing written" in capsys.readouterr().out


def test_the_interactive_path_writes_what_was_answered(tmp_path):
    root = tmp_path / ".shanty"
    rc = cli._cmd_init(
        _Args(root),
        ask=_Ask("sattler", "arnold", "", "heavy", "yes", "75", "yes"),
        isatty=lambda: True)
    assert rc == cli.OK
    assert _card(root, "arnold")["reports_to"] == "sattler"
    cfg = config.load(root)
    assert cfg.mode == "heavy" and cfg.hibernate.max_quiet_minutes == 75


# --- and then the town starts ----------------------------------------------

def test_init_then_start_brings_up_the_admin(tmp_path, monkeypatch, capsys):
    """The end-to-end claim: a fresh store, one wizard, and `st start` works —
    with no hand-edited JSON anywhere in between."""
    root = tmp_path / ".shanty"
    assert cli._cmd_init(_Args(root, yes=True, admin="sattler",
                               crew="arnold,billy")) == cli.OK

    class _Panes:
        live: set = set()

        def exists(self, pane):
            return pane in self.live

    panes = _Panes()
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    monkeypatch.setattr(cli, "_runtime", lambda *_a, **_k: object())
    launched = []

    def fake_launch(_a, card, _p, _r, *, dry_run=False):
        launched.append((card.name, card.pane))
        panes.live.add(card.pane)
        return cli.OK

    monkeypatch.setattr(cli, "_launch", fake_launch)
    assert cli.main(["--root", str(root), "start"]) == cli.OK
    assert launched == [("sattler", "st-sattler")], "lite: the admin, alone"
