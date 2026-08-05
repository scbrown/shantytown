"""`[crew.<name>] role` accepts what `[roles.*]` declares (GitHub #37 residual).

THE DISAGREEMENT THIS CLOSES. `tier.plan_role_set` stopped using the closed enum
when #37's trait model landed — it asks `catalog.describes(role)`. `config._crew`
did not, so one file could declare a role and then be refused for using it:

    [roles.advisor]              # declared here...
    attachment = "unattached"

    [crew.malcolm]
    role = "advisor"             # ...and REFUSED here, three lines down

while `st roles set malcolm advisor` succeeded, because that path goes through the
catalog. Two paths to one fact, disagreeing, and the file-authored one lost — which
is the shape the trait model exists to kill, surviving in the last place nobody
looked because `VALID_ROLES` still lived at the top of tier.py and still read like
the vocabulary.

The contract:
  · a role declared in [roles.*] is assignable in [crew.*] of the same file
  · the built-in three still work with no [roles.*] at all
  · a role NOBODY declares is still refused, and the message says how to declare it
  · the refusal lists what IS allowed, built-ins and declared together
"""
from __future__ import annotations

import pytest

from shantytown import config


def _write(tmp_path, text):
    p = tmp_path / "shantytown.toml"
    p.write_text(text)
    return tmp_path


def test_a_declared_role_is_assignable_in_the_same_file(tmp_path):
    """The bug, exactly: declare it and use it."""
    root = _write(tmp_path, '''
[roles.advisor]
attachment = "unattached"

[crew.malcolm]
role = "advisor"
''')
    cfg = config.load(root)
    assert cfg.crew["malcolm"].role == "advisor"
    assert "advisor" in cfg.roles, "the role table must still be parsed as before"


def test_the_declared_role_really_is_unattached_end_to_end(tmp_path):
    """Not just accepted as a STRING — it has to compose to the traits that make
    `advisor` legal, or this only moved the disagreement somewhere quieter."""
    root = _write(tmp_path, '''
[roles.advisor]
attachment = "unattached"

[crew.malcolm]
role = "advisor"
''')
    cat = config.load(root).catalog()
    assert cat.describes("advisor")
    assert cat.of("advisor").unattached is True


def test_the_builtin_three_still_work_with_no_roles_table(tmp_path):
    """POSITIVE CONTROL for the fallback. A deployment that declares nothing gets
    exactly what it got before."""
    root = _write(tmp_path, '''
[crew.ellie]
role = "worker"

[crew.sattler]
role = "administrator"
''')
    cfg = config.load(root)
    assert cfg.crew["ellie"].role == "worker"
    assert cfg.crew["sattler"].role == "administrator"


def test_an_undeclared_role_is_still_refused_and_says_how_to_declare_it(tmp_path):
    """The gate must still be a gate. A refusal that does not say what to do is
    how somebody concludes the role model is broken and hand-edits a card."""
    root = _write(tmp_path, '''
[crew.malcolm]
role = "advisor"
''')
    with pytest.raises(config.ConfigError) as ei:
        config.load(root)
    msg = str(ei.value)
    assert "advisor" in msg
    assert "[roles.advisor]" in msg, "the refusal must name the fix"
    assert "worker" in msg and "administrator" in msg, "and what IS allowed"


def test_the_refusal_lists_declared_roles_alongside_the_builtins(tmp_path):
    """When some roles ARE declared, the allowed list must include them — a
    refusal that names only the built-in three would tell the operator their own
    declarations do not count, which was the bug's actual message."""
    root = _write(tmp_path, '''
[roles.advisor]
attachment = "unattached"

[crew.malcolm]
role = "observer"
''')
    with pytest.raises(config.ConfigError) as ei:
        config.load(root)
    assert "advisor" in str(ei.value)
