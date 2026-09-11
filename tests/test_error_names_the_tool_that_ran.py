"""An error must name the binary that ACTUALLY RAN (aegis-7okaae).

BrTracker inherits nearly every method from BeadsTracker and overrides only the
runner, so a hardcoded "bd" in an inherited error message reported a RETIRED tool
for a failure the LIVE one produced.

That is not cosmetic. A codex worker's read-only-store failure surfaced to a
coordinator as `bd create failed: bd exited 7 with no output`, which reads as a
regression back onto the dead Dolt store — and the filed bug said so. The real
cause was the sandbox policy. The message sent the reader at the wrong defect.
"""
from __future__ import annotations

import subprocess

import pytest

from shantytown.beads import BeadsTracker, _bd_error_text, _bd_failure
from shantytown.br import BrTracker


def _failed(code=7, out="", err=""):
    return subprocess.CompletedProcess(["x"], code, out, err)


def test_br_declares_br_and_beads_declares_bd():
    assert BrTracker._tool == "br"
    assert BeadsTracker._tool == "bd"


def test_the_message_builders_name_the_tool():
    assert "br exited 7" in _bd_error_text(_failed(), tool="br")
    # CONTROL: the default is unchanged for the bd backend.
    assert "bd exited 7" in _bd_error_text(_failed())


def test_a_validation_refusal_names_the_tool():
    # the reason is read from br's JSON envelope on stdout, not bare text
    e = _bd_failure("br create",
                    _failed(out='{"error": "validation failed: bad title"}'),
                    tool="br")
    assert "refused by br" in str(e)
    assert "refused by bd" not in str(e)


def test_a_br_create_failure_does_NOT_say_bd(monkeypatch, tmp_path):
    """THE REGRESSION, end to end through the inherited create()."""
    t = BrTracker(repo=str(tmp_path))
    monkeypatch.setattr(t, "_bd_for", lambda *a, **k: _failed(), raising=False)
    monkeypatch.setattr(t, "_bd", lambda *a, **k: _failed())

    with pytest.raises(RuntimeError) as e:
        t.create("anything")
    msg = str(e.value)
    assert "br create failed" in msg, msg
    assert "bd" not in msg.replace("br", ""), (
        f"an error from br must not name bd — that is what sent a coordinator "
        f"hunting a retired-store regression: {msg!r}")


def test_the_bd_backend_still_says_bd(monkeypatch, tmp_path):
    """CONTROL. Without this, renaming everything to 'br' would pass the test
    above while making the bd backend lie in the other direction."""
    t = BeadsTracker(repo=str(tmp_path))
    monkeypatch.setattr(t, "_bd_for", lambda *a, **k: _failed(), raising=False)
    monkeypatch.setattr(t, "_bd", lambda *a, **k: _failed())
    with pytest.raises(RuntimeError) as e:
        t.create("anything")
    assert "bd create failed" in str(e.value)
