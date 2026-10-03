"""Fleet-wide tend sweeps run on the fleet owner host only (aegis-1g55ji).

A second host's tend (the Mac) read the shared board and pushed every fleet-wide
nag to its OWN admin: the deferral sweep, governor advisories, stale-BLOCKED,
deferred-but-unblocked. The vati tend already routes those to the coordinator.
"""
import re
from pathlib import Path
from types import SimpleNamespace

from shantytown import cli


def _cfg(name, owner):
    return SimpleNamespace(host_name=name, host_admission_owner=owner)


def test_the_owner_host_runs_fleet_sweeps():
    assert cli.runs_fleet_sweeps(_cfg("vati", "vati"))


def test_a_non_owner_host_does_not():
    assert not cli.runs_fleet_sweeps(_cfg("macbookair-stiwi", "vati"))


def test_a_single_host_with_no_declared_owner_still_runs_them():
    assert cli.runs_fleet_sweeps(_cfg("solo", None))


def test_every_fleet_sweep_label_goes_through_the_owner_gate():
    src = Path(cli.__file__).read_text()
    for label in cli.FLEET_SWEEPS:
        assert f'_fleet_sweep("{label}"' in src, label
        assert not re.search(rf'(?<!_fleet)_sweep\("{label}"', src), label
