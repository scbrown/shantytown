# Push freeze: before and after

Trigger: publish a feature to two approved private bare peers while the
operator has recorded `st.push-freeze.local`. Base advances BOTH refs; candidate
refuses before either advances. Removing the hold permits both on each version
(the positive control).

This is an output-only terminal recording, not a production deployment. Base is
22c563e64d2026389df15c043f4f55943bca91af; the recording prints the SHA256 of the
candidate workspace.py bytes so it remains checkable after documentation edits.
All writes are to temporary local Git fixtures, with no network endpoint.

From the checkout:

```bash
PYTHONPATH=. python3 docs/demos/push-freeze/run.py
scriptreplay --log-timing docs/demos/push-freeze/timing.log --log-out docs/demos/push-freeze/output.log
```

The small [recording](output.log) and [timing](timing.log) are retained here for
review and replay with util-linux scriptreplay. The output is also a readable
transcript. A recorded local hold does not prove that every operator has set
all production hostname aliases; installation and fleet coordination remain
separate gates.
