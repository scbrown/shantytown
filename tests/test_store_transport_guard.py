"""A fake startup must not reach a real board through a newer transport."""
import pytest
from shantytown import beads, br, sd


@pytest.mark.parametrize('tracker,method,args', [
    (beads.BeadsTracker, '_bd', ('list',)),
    (br.BrTracker, '_bd', ('list',)),
    (br.BrTracker, '_bd_in', (None, 'list')),
    (sd.SdTracker, '_bd_in', (None, 'list')),
    (sd.SdTracker, 'prove_store', ()),
])
def test_every_real_transport_is_refused_by_default(tracker, method, args):
    with pytest.raises(AssertionError, match='test shelled out'):
        getattr(tracker(), method)(*args)
