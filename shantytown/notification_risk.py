"""Interrupt only for failures at the warning risk floor.

Routine state changes remain observable in scheduled logs. Unknown failure
priority stays loud; an incomplete read cannot earn a low-risk verdict.
"""

MINIMUM_RISK = 2


def interrupts(*, failure: bool, risk: int) -> bool:
    return failure and risk >= MINIMUM_RISK


def deferred_risk(priority) -> int:
    if type(priority) is not int or not 0 <= priority <= 4:
        return 3
    return 3 if priority == 0 else 2 if priority == 1 else 1
