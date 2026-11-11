"""OPEN-10 settlement treatment and the variance calculation (spec 7.4, AC-REC-07, AC-REC-15)."""

import pytest

from packs.finops.money import Money, MoneyError
from packs.finops.settlement import (
    SettlementLine,
    category,
    contribution,
    settlement_variance,
)


def usd(amount: str) -> Money:
    return Money.parse(amount, "USD")


def line(reference: str, entry_type: str, gross: str, fee: str | None = None,
         net: str | None = None, currency: str = "USD") -> SettlementLine:
    return SettlementLine(
        reference,
        entry_type,
        Money.parse(gross, currency),
        Money.parse(fee, currency) if fee is not None else None,
        Money.parse(net, currency) if net is not None else None,
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("T0006", "CAPTURE"),
        ("T1107", "REFUND"),
        ("T0113", "FEE"),
        ("T1201", "CHARGEBACK"),
        ("T1202", "ADJUSTMENT"),
        ("T1900", "ADJUSTMENT"),
        ("T0400", "EXCLUDED"),
        ("T1503", "EXCLUDED"),
        ("t0006", "CAPTURE"),
        ("T9999", None),
    ],
)
def test_event_codes_follow_the_open_10_table(code, expected):
    assert category(code) == expected


def test_a_reported_net_already_includes_its_fee():
    # Gross 100.00 with a 3.20 fee reported as net 96.80: the fee must not come off again.
    assert contribution(line("a", "T0006", "100.00", fee="-3.20", net="96.80")) == usd("96.80")


def test_without_a_net_the_fee_is_subtracted_once():
    assert contribution(line("a", "T0006", "50.00", fee="-1.75")) == usd("48.25")


def test_refund_reduces_the_net_once():
    assert contribution(line("r", "T1107", "-20.00", fee="0.58", net="-19.42")) == usd("-19.42")


@pytest.mark.parametrize(
    "entry",
    [
        line("x", "T9999", "10.00"),
        line("x", "T1107", "20.00"),  # a refund reported as a credit contradicts its category
        line("x", "T0006", "-5.00"),  # a capture reported as a debit likewise
    ],
)
def test_an_entry_that_cannot_be_classified_is_not_guessed(entry):
    assert contribution(entry) is None


def test_expected_net_and_variance_never_double_subtract_fees():
    lines = [
        line("cap-1", "T0006", "100.00", fee="-3.20", net="96.80"),
        line("cap-2", "T0006", "50.00", fee="-1.75"),
        line("ref-1", "T1107", "-20.00", fee="0.58", net="-19.42"),
        line("fee-1", "T0113", "-2.00"),
        line("cb-1", "T1201", "-10.00", net="-10.00"),
        line("adj-1", "T1202", "1.50"),
        line("wd-1", "T0400", "-116.13"),
        line("odd-1", "T9999", "4.00"),
    ]
    result = settlement_variance(lines, bank_credit=usd("114.13"))
    # 96.80 + 48.25 - 19.42 - 2.00 - 10.00 + 1.50 = 115.13. Double-subtracting the fees
    # would give 111.93 instead.
    assert result.expected_net == usd("115.13")
    assert result.variance == usd("1.00")
    assert result.excluded == ["wd-1"]
    assert result.unclassified == ["odd-1"]
    assert "cap-1" in result.included


def test_variance_is_zero_when_the_bank_credit_matches():
    lines = [line("cap-1", "T0006", "100.00", fee="-3.20", net="96.80")]
    assert settlement_variance(lines, usd("96.80")).variance == usd("0")


def test_different_currencies_are_never_settled_together():
    lines = [line("cap-1", "T0006", "100.00", currency="EUR")]
    with pytest.raises(MoneyError):
        settlement_variance(lines, usd("100.00"))


def test_six_decimal_places_survive():
    lines = [line("cap-1", "T0006", "0.000001")]
    assert settlement_variance(lines, usd("0")).variance == usd("0.000001")
