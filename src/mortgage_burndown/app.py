"""Mortgage burndown web UI."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from flask import Flask, render_template, request

from mortgage_burndown.currency import (
    CURRENCIES,
    DEFAULT_CURRENCY_CODE,
    Currency,
    format_compact_amount,
    get_currency,
)
from mortgage_burndown.mortgage import (
    balance_after_n_payments,
    calculate_mortgage,
    count_payments_made,
    first_payment_date_for_payments_made,
    payment_date_for_month_index,
    payment_month_index_for_date,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
app = Flask(__name__, template_folder=str(_ROOT / "templates"))

_CURRENCY_OPTIONS = sorted(CURRENCIES.items(), key=lambda kv: kv[1].label)


def _chart_html(df, c: Currency, *, has_dates: bool) -> str:
    sym = c.symbol
    if has_dates and "Payment date" in df.columns:
        x = pd.to_datetime(df["Payment date"], format="%Y-%m-%d")
        if c.position == "before":
            hover = f"%{{x|%Y-%m-%d}}<br>{sym}%{{y:,.0f}}<extra></extra>"
        else:
            hover = f"%{{x|%Y-%m-%d}}<br>%{{y:,.0f}} {sym}<extra></extra>"
        xaxis_title = "Payment date"
    else:
        x = df["Month"] / 12.0
        if c.position == "before":
            hover = f"%{{x:.2f}} yr<br>{sym}%{{y:,.0f}}<extra></extra>"
        else:
            hover = f"%{{x:.2f}} yr<br>%{{y:,.0f}} {sym}<extra></extra>"
        xaxis_title = "Years since start"
    fig = go.Figure()
    accent = "#38bdf8"
    accent_fill = "rgba(56, 189, 248, 0.18)"
    fig.add_trace(
        go.Scatter(
            x=x,
            y=df["Balance"],
            mode="lines",
            name="Remaining balance",
            line=dict(color=accent, width=2.5),
            fill="tozeroy",
            fillcolor=accent_fill,
            hovertemplate=hover,
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15, 23, 42, 0.45)",
        font=dict(family="Plus Jakarta Sans, ui-sans-serif, system-ui", color="#94a3b8", size=12),
        margin=dict(l=56, r=24, t=48, b=52),
        title=dict(
            text="Balance over time",
            font=dict(size=15, color="#e2e8f0", family="Plus Jakarta Sans, sans-serif"),
        ),
        xaxis_title=xaxis_title,
        yaxis_title=f"Balance ({sym})",
        xaxis=dict(gridcolor="rgba(148, 163, 184, 0.12)", linecolor="rgba(148, 163, 184, 0.2)"),
        yaxis=dict(gridcolor="rgba(148, 163, 184, 0.12)", linecolor="rgba(148, 163, 184, 0.2)"),
        hovermode="x unified",
        showlegend=False,
        height=420,
    )
    if c.position == "before":
        fig.update_yaxes(tickformat=",.0f", tickprefix=sym)
    else:
        fig.update_yaxes(tickformat=",.0f", ticksuffix=f" {sym}")
    fig.update_xaxes(title=dict(font=dict(color="#94a3b8")))
    fig.update_yaxes(title=dict(font=dict(color="#94a3b8")))
    return fig.to_html(full_html=False, include_plotlyjs="cdn", config={"displayModeBar": True})


def _table_html(df, c: Currency, *, has_prepayments: bool) -> str:
    sym = c.symbol
    rename = {
        "Payment": f"Payment ({sym})",
        "Prepayment": f"Prepayment ({sym})",
        "Principal": f"Principal ({sym})",
        "Interest": f"Interest ({sym})",
        "Balance": f"Balance ({sym})",
    }
    out = df.rename(columns=rename)
    if not has_prepayments and f"Prepayment ({sym})" in out.columns:
        out = out.drop(columns=[f"Prepayment ({sym})"])
    cols = list(out.columns)
    lead: list[str] = []
    if "Payment #" in cols:
        cols.remove("Payment #")
        lead.append("Payment #")
    if "Payment date" in cols:
        cols.remove("Payment date")
        lead.append("Payment date")
    if lead:
        out = out[lead + cols]
    return out.to_html(
        classes="data-table",
        index=False,
        border=0,
        float_format=lambda x: f"{x:,.2f}" if isinstance(x, float) else str(x),
    )


def _yearly_interest_rows(
    df: pd.DataFrame,
    c: Currency,
    *,
    payment_anchor: date | None,
) -> tuple[list[tuple[int, str]], bool]:
    """
    Sum interest by calendar year when payment dates or a first-payment anchor exist;
    otherwise fall back to loan-year buckets (Year column).
    Second value is True when rows use calendar years.
    """
    if df is None or len(df) == 0 or "Interest" not in df.columns:
        return [], True

    if "Payment date" in df.columns:
        years = pd.to_datetime(df["Payment date"], format="%Y-%m-%d").dt.year
        tmp = df.copy()
        tmp["_cal_year"] = years
        agg = tmp.groupby("_cal_year", sort=True)["Interest"].sum()
        rows = [(int(y), format_compact_amount(float(t), c)) for y, t in agg.items()]
        return rows, True

    if payment_anchor is not None:
        years = pd.Series(
            [payment_date_for_month_index(payment_anchor, int(m)).year for m in df["Month"]],
            index=df.index,
        )
        tmp = df.copy()
        tmp["_cal_year"] = years
        agg = tmp.groupby("_cal_year", sort=True)["Interest"].sum()
        rows = [(int(y), format_compact_amount(float(t), c)) for y, t in agg.items()]
        return rows, True

    if "Year" not in df.columns:
        return [], True
    agg = df.groupby("Year", sort=True)["Interest"].sum()
    rows = [(int(y), format_compact_amount(float(t), c)) for y, t in agg.items()]
    return rows, False


DEFAULTS = {
    # Disbursement / original loan amount; current balance is derived after payments to date when set.
    "original_principal": 7_500_000,
    "years": 20,
    "rate_pct": 8.35,
    # Bank “completed term” (months paid); seeds default first EMI date (with reference date below).
    "months_completed": 63,
    "payment_day": 16,
}

# Calendar date when the bank reported completed vs remaining term; used only to derive the
# default first EMI so remaining = years×12 − count_payments_made(first_emi, today) without
# hardcoding a “remaining months” number.
REFERENCE_AS_OF_BANK = date(2026, 4, 9)


def _default_first_emi_date() -> date:
    return first_payment_date_for_payments_made(
        REFERENCE_AS_OF_BANK,
        DEFAULTS["months_completed"],
        payment_day=DEFAULTS["payment_day"],
    )


def _parse_start_date(raw: str | None) -> date | None:
    if not raw or not str(raw).strip():
        return None
    try:
        return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _schedule_start_date(loan_start_d: date | None, months_paid: int) -> date | None:
    """First payment date of the forward schedule (month 1): next EMI after completed payments."""
    if loan_start_d is None:
        return None
    if months_paid <= 0:
        return loan_start_d
    return payment_date_for_month_index(loan_start_d, months_paid + 1)


def _schedule_blurb(
    loan_start_d: date | None,
    months_paid: int,
    start_d: date | None,
    *,
    has_rows: bool,
) -> str | None:
    """Explain that the table is remaining payments; first row is next due date when loan is in progress."""
    if not has_rows or loan_start_d is None or start_d is None:
        return None
    first = loan_start_d.isoformat()
    nxt = start_d.isoformat()
    if months_paid <= 0:
        return (
            f"Payment dates begin at your first EMI ({first}). Each row is one scheduled payment from today’s balance."
        )
    return (
        f"First EMI was {first}. You have already made {months_paid} payment(s); "
        f"the first row below is your next due date ({nxt}), not the original first payment."
    )


def _parse_event_dates(
    dates: list[str],
    values: list[str],
    *,
    start_d: date | None,
    months_total: int,
    as_rate: bool,
) -> tuple[dict[int, float], str | None]:
    """
    Build month-indexed dict from parallel date + value lists.
    as_rate: True -> values are percent, stored as decimal; False -> currency amounts.
    Returns (dict, error_message).
    """
    out: dict[int, float] = {}
    for d_raw, v_raw in zip(dates, values):
        d_raw = (d_raw or "").strip()
        v_raw = (v_raw or "").strip()
        if not d_raw and not v_raw:
            continue
        if not d_raw or not v_raw:
            return {}, "Each row needs both an effective date and a value."
        ev = _parse_start_date(d_raw)
        if ev is None:
            return {}, "Invalid effective date."
        if start_d is None:
            return {}, "Set a first payment date to use rate changes or prepayments on specific dates."
        mi = payment_month_index_for_date(start_d, ev, max_months=months_total)
        if mi is None:
            return {}, "An effective date is after the last scheduled payment; remove or adjust it."
        try:
            if as_rate:
                out[mi] = float(v_raw) / 100.0
            else:
                out[mi] = float(v_raw)
        except ValueError:
            return {}, "Invalid amount."
    return out, None


@app.route("/", methods=["GET", "POST"])
def index():
    form_error: str | None = None
    months_paid: int = 0
    if request.method == "POST":
        original_raw = (request.form.get("original_principal") or "").strip()
        if not original_raw:
            form_error = form_error or "Original principal is required."
            original_principal_in = float(DEFAULTS["original_principal"])
        else:
            try:
                original_principal_in = float(original_raw)
                if original_principal_in <= 0:
                    form_error = form_error or "Original principal must be a positive number."
                    original_principal_in = float(DEFAULTS["original_principal"])
            except ValueError:
                form_error = form_error or "Original principal must be a valid number."
                original_principal_in = float(DEFAULTS["original_principal"])
        years = int(request.form.get("years", DEFAULTS["years"]))
        rate_pct = float(request.form.get("rate_pct", DEFAULTS["rate_pct"]))
        annual_rate = rate_pct / 100.0

        rd = request.form.getlist("rate_date[]")
        rv = request.form.getlist("rate_value[]")
        prep_dates = request.form.getlist("prepay_date[]")
        pv = request.form.getlist("prepay_value[]")

        loan_start_raw = request.form.get("loan_start_date") or ""
        loan_start_d = _parse_start_date(loan_start_raw)
        today = date.today()

        if loan_start_d is not None and loan_start_d <= today:
            total_term = years * 12
            months_paid = count_payments_made(loan_start_d, today)
        else:
            months_paid = 0

        if months_paid > 0:
            months_total = max(1, years * 12 - months_paid)
        else:
            months_total = years * 12

        start_d = _schedule_start_date(loan_start_d, months_paid)

        principal = float(original_principal_in)
        if loan_start_d is not None:
            hist_rc, err_hr = _parse_event_dates(
                rd, rv, start_d=loan_start_d, months_total=years * 12, as_rate=True
            )
            hist_pp, err_hp = _parse_event_dates(
                prep_dates, pv, start_d=loan_start_d, months_total=years * 12, as_rate=False
            )
            form_error = err_hr or err_hp or form_error
            if not form_error:
                if months_paid <= 0:
                    principal = original_principal_in
                else:
                    principal = balance_after_n_payments(
                        original_principal_in,
                        annual_rate,
                        years,
                        months_total=years * 12,
                        rate_changes=hist_rc,
                        prepayments=hist_pp,
                        n_payments=months_paid,
                    )
        # No first EMI yet: current balance equals original disbursement.
        else:
            principal = original_principal_in

        rate_changes: dict[int, float] = {}
        prepayments: dict[int, float] = {}
        if start_d is not None:
            rate_changes, err_r = _parse_event_dates(
                rd, rv, start_d=start_d, months_total=months_total, as_rate=True
            )
            prepayments, err_p = _parse_event_dates(
                prep_dates, pv, start_d=start_d, months_total=months_total, as_rate=False
            )
            form_error = err_r or err_p or form_error
        else:
            if any((d or "").strip() or (v or "").strip() for d, v in zip(rd, rv)) or any(
                (d or "").strip() or (v or "").strip() for d, v in zip(prep_dates, pv)
            ):
                form_error = form_error or (
                    "Set loan start date (first EMI) to use rate changes or prepayments on specific dates."
                )

        rate_rows = list(zip(rd, rv)) if rd else [("", "")]
        prepay_rows = list(zip(prep_dates, pv)) if prep_dates else [("", "")]
        if not rate_rows:
            rate_rows = [("", "")]
        if not prepay_rows:
            prepay_rows = [("", "")]

        raw_cc = (request.form.get("currency") or DEFAULT_CURRENCY_CODE).strip().upper()
        currency_code = raw_cc if raw_cc in CURRENCIES else DEFAULT_CURRENCY_CODE
        ctx = {
            "original_principal": original_principal_in,
            "principal": principal,
            "years": years,
            "rate_pct": rate_pct,
            "rate_rows": rate_rows,
            "prepay_rows": prepay_rows,
            "currency_code": currency_code,
            "loan_start_date": loan_start_raw,
            "form_error": form_error,
        }
    else:
        years = DEFAULTS["years"]
        rate_pct = DEFAULTS["rate_pct"]
        annual_rate = rate_pct / 100.0
        rate_changes: dict[int, float] = {}
        prepayments: dict[int, float] = {}
        form_error = None
        today = date.today()
        loan_start_d = _default_first_emi_date()
        loan_start_raw = loan_start_d.isoformat()
        months_paid = count_payments_made(loan_start_d, today)
        total_term_months = years * 12
        months_total = max(1, total_term_months - months_paid)
        original_principal = float(DEFAULTS["original_principal"])
        if months_paid <= 0:
            principal = original_principal
        else:
            principal = balance_after_n_payments(
                original_principal,
                annual_rate,
                years,
                months_total=years * 12,
                rate_changes={},
                prepayments={},
                n_payments=months_paid,
            )
        ctx = {
            "original_principal": original_principal,
            "principal": principal,
            "years": years,
            "rate_pct": rate_pct,
            "rate_rows": [("", "")],
            "prepay_rows": [("", "")],
            "currency_code": DEFAULT_CURRENCY_CODE,
            "loan_start_date": loan_start_raw,
            "form_error": form_error,
        }

    currency = get_currency(ctx.get("currency_code"))
    if request.method == "POST" and form_error:
        rate_changes, prepayments = {}, {}

    df = calculate_mortgage(
        principal,
        annual_rate,
        years,
        months_total=months_total,
        rate_changes=rate_changes,
        prepayments=prepayments,
    )

    df_no_prepay = calculate_mortgage(
        principal,
        annual_rate,
        years,
        months_total=months_total,
        rate_changes=rate_changes,
        prepayments={},
    )
    df_no_rate_change = calculate_mortgage(
        principal,
        annual_rate,
        years,
        months_total=months_total,
        rate_changes={},
        prepayments=prepayments,
    )

    interest_no_prepay = float(df_no_prepay["Interest"].sum()) if len(df_no_prepay) else 0.0
    interest_actual = float(df["Interest"].sum()) if len(df) else 0.0
    interest_saved_prepay = interest_no_prepay - interest_actual

    # Same prepayments, but no rate changes: baseline for “what if rate never moved?”
    interest_total_no_rate = float(df_no_rate_change["Interest"].sum()) if len(df_no_rate_change) else 0.0
    # Positive => actual schedule pays less total interest than that baseline (typical after a rate cut).
    interest_delta_vs_fixed_rate = interest_total_no_rate - interest_actual

    months_actual = len(df)
    months_no_rate = len(df_no_rate_change)
    months_delta_rate = months_no_rate - months_actual

    has_prep = bool(prepayments)
    has_rate_changes = bool(rate_changes)
    if has_prep:
        interest_saved_display = format_compact_amount(max(0.0, interest_saved_prepay), currency)
    else:
        interest_saved_display = "—"
    # Payment is fixed at origination; rate path changes interest vs. starting rate only.
    if has_rate_changes:
        rate_vs_fixed_display = format_compact_amount(interest_delta_vs_fixed_rate, currency)
        if months_delta_rate != 0:
            if months_delta_rate > 0:
                rate_vs_fixed_sub = f"{months_delta_rate} mo sooner payoff · vs. starting rate only"
            else:
                rate_vs_fixed_sub = f"{-months_delta_rate} mo longer payoff · vs. starting rate only"
        else:
            rate_vs_fixed_sub = "Same payoff length; positive = less interest vs. starting rate only"
    else:
        rate_vs_fixed_display = "—"
        rate_vs_fixed_sub = ""

    loan_start_d = _parse_start_date(ctx.get("loan_start_date"))
    start_d = _schedule_start_date(loan_start_d, months_paid)
    if start_d is not None and len(df):
        df = df.copy()
        dates = [payment_date_for_month_index(start_d, int(m)).isoformat() for m in df["Month"]]
        if loan_start_d is not None:
            df.insert(0, "Payment #", [months_paid + int(m) for m in df["Month"]])
            df.insert(1, "Payment date", dates)
        else:
            df.insert(0, "Payment date", dates)

    has_dates = start_d is not None and len(df) and "Payment date" in df.columns
    schedule_blurb = _schedule_blurb(
        loan_start_d, months_paid, start_d, has_rows=bool(len(df))
    )
    table_html = _table_html(df, currency, has_prepayments=has_prep)
    chart = _chart_html(df, currency, has_dates=has_dates)

    total_regular = float(df["Payment"].sum()) if len(df) else 0.0
    total_prepaid = float(df["Prepayment"].sum()) if len(df) and "Prepayment" in df.columns else 0.0
    total_paid = total_regular + total_prepaid
    months_to_payoff = len(df)

    payments_remaining_display = months_total
    yr_remaining = round(months_total / 12.0, 2)
    if months_to_payoff < months_total:
        payments_remaining_sub = f"~{yr_remaining} yr · early payoff at {months_to_payoff} mo"
    else:
        payments_remaining_sub = f"~{yr_remaining} yr"

    total_term = years * 12
    summary = {
        "months": months_to_payoff,
        "term_months": months_total,
        "total_term": total_term,
        "months_paid": months_paid,
        "years_paid": round(months_to_payoff / 12.0, 2) if months_to_payoff else 0.0,
        "total_paid": total_paid,
        "payments_remaining_display": payments_remaining_display,
        "payments_remaining_sub": payments_remaining_sub,
    }
    total_paid_display = format_compact_amount(total_paid, currency)
    principal_compact = format_compact_amount(principal, currency)
    _op = ctx.get("original_principal")
    original_principal_compact = format_compact_amount(float(_op), currency) if _op is not None else ""

    payment_anchor = start_d or loan_start_d
    yearly_interest_rows, yearly_interest_by_calendar = _yearly_interest_rows(
        df, currency, payment_anchor=payment_anchor
    )
    total_interest = float(df["Interest"].sum()) if len(df) else 0.0
    yearly_interest_total_display = format_compact_amount(total_interest, currency)

    return render_template(
        "index.html",
        chart_html=chart,
        table_html=table_html,
        schedule_blurb=schedule_blurb,
        yearly_interest_rows=yearly_interest_rows,
        yearly_interest_by_calendar=yearly_interest_by_calendar,
        yearly_interest_total_display=yearly_interest_total_display,
        summary=summary,
        principal_compact=principal_compact,
        original_principal_compact=original_principal_compact,
        total_paid_display=total_paid_display,
        interest_saved_display=interest_saved_display,
        rate_vs_fixed_display=rate_vs_fixed_display,
        rate_vs_fixed_sub=rate_vs_fixed_sub,
        currencies=_CURRENCY_OPTIONS,
        currency=currency,
        **ctx,
    )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
