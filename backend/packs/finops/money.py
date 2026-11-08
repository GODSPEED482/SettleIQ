"""Decimal-safe money matching NUMERIC(20,6) (spec 6.3, ARCH-04).

Rejects floats, excess precision and cross-currency arithmetic instead of rounding or
converting. Rounding and FX rules are added explicitly under OPEN-07.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_MAX_ABS = Decimal(10) ** 14
_SCALE = 6


class MoneyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise MoneyError("amount must be a Decimal")
        if not self.amount.is_finite():
            raise MoneyError("amount must be finite")
        if abs(self.amount) >= _MAX_ABS:
            raise MoneyError("amount exceeds NUMERIC(20,6)")
        if self.amount != self.amount.quantize(Decimal(1).scaleb(-_SCALE)):
            raise MoneyError("amount has more than 6 decimal places")
        if not _CURRENCY.match(self.currency):
            raise MoneyError(f"invalid currency: {self.currency!r}")

    @classmethod
    def parse(cls, amount: str, currency: str) -> "Money":
        try:
            return cls(Decimal(amount), currency)
        except InvalidOperation:
            raise MoneyError(f"invalid amount: {amount!r}") from None

    def _same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise MoneyError(f"cannot combine {self.currency} with {other.currency}")

    def __add__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)
