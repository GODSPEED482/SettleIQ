"""Settlement calculation under the OPEN-10 treatment (spec 7.4, AC-REC-07, AC-REC-15).

The domain engine and the `calculate_settlement_variance` agent tool (task 5.7) both use this,
so there is one calculation. Everything is in Money: no floats, no cross-currency arithmetic.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from pathlib import Path

import yaml

from packs.finops.money import Money, MoneyError

TREATMENT_FILE = Path(__file__).with_name("settlement_treatment.yaml")


@dataclass(frozen=True)
class SettlementLine:
    """One provider report entry. `net`, when reported, already includes `fee`."""

    reference: str
    entry_type: str
    gross: Money | None
    fee: Money | None = None
    net: Money | None = None


@dataclass(frozen=True)
class SettlementResult:
    expected_net: Money
    variance: Money
    included: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)


@cache
def treatment() -> dict:
    return yaml.safe_load(TREATMENT_FILE.read_text(encoding="utf-8"))


def category(entry_type: str) -> str | None:
    table = treatment()
    code = entry_type.strip().upper()
    if code in table["codes"]:
        return table["codes"][code]
    return table["families"].get(code[:3])


def _sign_matches(sign: str, amount: Decimal) -> bool:
    if sign == "positive":
        return amount > 0
    if sign == "negative":
        return amount < 0
    return True


def contribution(line: SettlementLine) -> Money | None:
    """The line's effect on Expected Net, or None when it must not be guessed."""
    name = category(line.entry_type)
    if name is None or line.gross is None:
        return None
    rule = treatment()["categories"][name]
    if not _sign_matches(rule["sign"], line.gross.amount):
        return None
    if not rule["included"]:
        return Money(Decimal(0), line.gross.currency)
    if line.net is not None:
        return line.net
    return line.gross + line.fee if line.fee is not None else line.gross


def settlement_variance(lines: list[SettlementLine], bank_credit: Money) -> SettlementResult:
    """Variance = Expected Net - Bank Credit. Mixed currencies raise MoneyError."""
    expected = Money(Decimal(0), bank_credit.currency)
    included, excluded, unclassified = [], [], []
    for line in sorted(lines, key=lambda item: item.reference):
        amount = contribution(line)
        if amount is None:
            unclassified.append(line.reference)
            continue
        if line.gross is not None and line.gross.currency != bank_credit.currency:
            raise MoneyError(f"cannot settle {line.gross.currency} into {bank_credit.currency}")
        if treatment()["categories"][category(line.entry_type)]["included"]:
            expected = expected + amount
            included.append(line.reference)
        else:
            excluded.append(line.reference)
    return SettlementResult(expected, expected - bank_credit, included, excluded, unclassified)
