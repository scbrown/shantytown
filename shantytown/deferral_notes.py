"""Pure single-marker notes preparation; retained prose is never discarded."""
import re

MARKER_PREFIX = re.compile(r"resume[_ -]?when\s*[:=]", re.IGNORECASE)

def historical_notes(text: str) -> str:
    return MARKER_PREFIX.sub("historical resume condition:", text)

def canonical_reason(reason: str, condition: str) -> str:
    from .deferrals import parse_condition
    marker = parse_condition(reason)
    if len(MARKER_PREFIX.findall(reason)) == 1 and marker and marker.render() == condition:
        return reason
    return historical_notes(reason).rstrip() + "\nresume_when: " + condition

def merge_deferral_notes(existing: str | None, addition: str) -> tuple[str, bool]:
    old = existing or ""
    if not old.strip():
        return addition, False
    if addition in old and len(MARKER_PREFIX.findall(old)) == 1:
        return old, False
    if old in addition and len(MARKER_PREFIX.findall(addition)) == 1:
        return addition, True
    return historical_notes(old) + "\n\n" + addition, True
