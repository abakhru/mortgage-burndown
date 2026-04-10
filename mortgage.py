"""Amortization with optional prepayments and rate changes on specific payment months."""

from __future__ import annotations

import calendar
from datetime import date

import pandas as pd


def add_months(d: date, months: int) -> date:
    """Add calendar months, clamping day to last day of month when needed."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def payment_date_for_month_index(start: date, month_index: int) -> date:
    """month_index 1 = first payment month (same calendar day rule as add_months)."""
    return add_months(start, month_index - 1)


def payment_month_index_for_date(
    start: date, event: date, *, max_months: int
) -> int | None:
    """
    Map a calendar date to the 1-based payment month: first payment whose date is on or after
    ``event``. Returns None if every payment in the schedule is strictly before ``event``.
    """
    if event <= start:
        return 1
    for m in range(1, max_months + 1):
        if payment_date_for_month_index(start, m) >= event:
            return m
    return None


def calculate_mortgage(
    principal: float,
    annual_rate: float,
    years: int,
    *,
    months_total: int | None = None,
    rate_changes: dict[int, float] | None = None,
    prepayments: dict[int, float] | None = None,
) -> pd.DataFrame:
    """
    months_total: if set, amortization length in months (overrides ``years * 12``). Use for an
    exact remaining term (e.g. 177) that is not a multiple of 12.

    rate_changes: payment month (1-based) -> new annual rate as decimal (e.g. 0.055 for 5.5%)
    prepayments: payment month -> lump sum at the start of that month (before that payment)

    Prepayment is applied first, then the rate may change for **interest** only. The **monthly
    payment is fixed** at the first payment (from initial principal, term, and starting rate)
    and never re-amortized. If the rate rises enough that interest exceeds that payment, the
    balance can grow (negative amortization) until a later rate or prepayment.
    """
    rate_changes = rate_changes or {}
    prepayments = prepayments or {}

    if months_total is not None:
        months_total = max(1, int(months_total))
    else:
        months_total = max(1, int(years) * 12)
    balance = float(principal)
    current_annual = float(annual_rate)
    monthly_rate = current_annual / 12.0

    def payment_for_balance(bal: float, m_left: int, mr: float) -> float:
        if bal <= 0 or m_left <= 0:
            return 0.0
        if mr <= 0:
            return bal / m_left
        return bal * (mr * (1 + mr) ** m_left) / ((1 + mr) ** m_left - 1)

    fixed_payment = payment_for_balance(balance, months_total, monthly_rate)

    rows: list[dict] = []

    for month in range(1, months_total + 1):
        year = (month - 1) // 12 + 1

        prep = prepayments.get(month, 0.0) or 0.0
        if prep > 0:
            balance = max(0.0, balance - prep)

        new_r = rate_changes.get(month)
        if new_r is not None:
            current_annual = float(new_r)
            monthly_rate = current_annual / 12.0

        if balance <= 0:
            break

        interest_payment = balance * monthly_rate
        principal_payment = fixed_payment - interest_payment

        if principal_payment >= balance:
            principal_payment = balance
            monthly_payment = interest_payment + principal_payment
            balance = 0.0
        elif principal_payment < 0:
            # Interest exceeds fixed payment: balance grows (negative amortization).
            monthly_payment = fixed_payment
            balance -= principal_payment
        else:
            monthly_payment = fixed_payment
            balance -= principal_payment

        rows.append(
            {
                "Month": month,
                "Year": year,
                "Payment": round(monthly_payment, 2),
                "Principal": round(principal_payment, 2),
                "Interest": round(interest_payment, 2),
                "Balance": round(max(0.0, balance), 2),
                "Rate (%)": round(current_annual * 100, 4),
            }
        )

        if balance <= 0:
            break

    return pd.DataFrame(rows)
