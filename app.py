"""Mortgage burndown web UI."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
from flask import Flask, render_template, request

from currency import CURRENCIES, Currency, DEFAULT_CURRENCY_CODE, format_compact_amount, get_currency
from mortgage import (
    calculate_mortgage,
    payment_date_for_month_index,
    payment_month_index_for_date,
)

app = Flask(__name__)

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


def _table_html(df, c: Currency) -> str:
    sym = c.symbol
    rename = {
        "Payment": f"Payment ({sym})",
        "Principal": f"Principal ({sym})",
        "Interest": f"Interest ({sym})",
        "Balance": f"Balance ({sym})",
    }
    out = df.rename(columns=rename)
    cols = list(out.columns)
    if "Payment date" in cols:
        cols.remove("Payment date")
        out = out[["Payment date"] + cols]
    return out.to_html(
        classes="data-table",
        index=False,
        border=0,
        float_format=lambda x: f"{x:,.2f}" if isinstance(x, float) else str(x),
    )


def _yearly_interest_rows(df: pd.DataFrame, c: Currency) -> list[tuple[int, str]]:
    """Loan year (1-based) -> display string for sum of interest that year."""
    if df is None or len(df) == 0 or "Year" not in df.columns:
        return []
    agg = df.groupby("Year", sort=True)["Interest"].sum()
    return [(int(y), format_compact_amount(float(t), c)) for y, t in agg.items()]


DEFAULTS = {
    "principal": 7_500_000,
    "years": 20,
    "rate_pct": 8.35,
}


def _parse_start_date(raw: str | None) -> date | None:
    if not raw or not str(raw).strip():
        return None
    try:
        return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


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
    if request.method == "POST":
        principal = float(request.form.get("principal", DEFAULTS["principal"]))
        years = int(request.form.get("years", DEFAULTS["years"]))
        rate_pct = float(request.form.get("rate_pct", DEFAULTS["rate_pct"]))
        annual_rate = rate_pct / 100.0

        rd = request.form.getlist("rate_date[]")
        rv = request.form.getlist("rate_value[]")
        pd = request.form.getlist("prepay_date[]")
        pv = request.form.getlist("prepay_value[]")

        remaining_months_raw = (request.form.get("remaining_months") or "").strip()
        if remaining_months_raw:
            try:
                months_total = max(1, min(600, int(float(remaining_months_raw))))
            except ValueError:
                months_total = years * 12
                if not form_error:
                    form_error = "Remaining term (months) must be a valid number."
        else:
            months_total = years * 12

        start_date_raw = request.form.get("start_date") or ""
        start_d = _parse_start_date(start_date_raw)

        rate_changes, err_r = _parse_event_dates(rd, rv, start_d=start_d, months_total=months_total, as_rate=True)
        prepayments, err_p = _parse_event_dates(pd, pv, start_d=start_d, months_total=months_total, as_rate=False)
        form_error = err_r or err_p or form_error

        rate_rows = list(zip(rd, rv)) if rd else [("", "")]
        prepay_rows = list(zip(pd, pv)) if pd else [("", "")]
        if not rate_rows:
            rate_rows = [("", "")]
        if not prepay_rows:
            prepay_rows = [("", "")]

        raw_cc = (request.form.get("currency") or DEFAULT_CURRENCY_CODE).strip().upper()
        currency_code = raw_cc if raw_cc in CURRENCIES else DEFAULT_CURRENCY_CODE
        ctx = {
            "principal": principal,
            "years": years,
            "rate_pct": rate_pct,
            "rate_rows": rate_rows,
            "prepay_rows": prepay_rows,
            "currency_code": currency_code,
            "start_date": start_date_raw,
            "form_error": form_error,
            "remaining_months": remaining_months_raw,
        }
    else:
        principal = DEFAULTS["principal"]
        years = DEFAULTS["years"]
        rate_pct = DEFAULTS["rate_pct"]
        annual_rate = rate_pct / 100.0
        rate_changes: dict[int, float] = {}
        prepayments: dict[int, float] = {}
        form_error = None
        months_total = years * 12
        ctx = {
            "principal": principal,
            "years": years,
            "rate_pct": rate_pct,
            "rate_rows": [("", "")],
            "prepay_rows": [("", "")],
            "currency_code": DEFAULT_CURRENCY_CODE,
            "start_date": "",
            "form_error": form_error,
            "remaining_months": "",
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
            rate_vs_fixed_sub = (
                "Same payoff length; positive = less interest vs. starting rate only"
            )
    else:
        rate_vs_fixed_display = "—"
        rate_vs_fixed_sub = ""

    start_d = _parse_start_date(ctx.get("start_date"))
    if start_d is not None and len(df):
        df = df.copy()
        df.insert(
            0,
            "Payment date",
            [payment_date_for_month_index(start_d, int(m)).isoformat() for m in df["Month"]],
        )

    has_dates = start_d is not None and len(df) and "Payment date" in df.columns
    table_html = _table_html(df, currency)
    chart = _chart_html(df, currency, has_dates=has_dates)

    total_paid = float(df["Payment"].sum()) if len(df) else 0.0
    months_to_payoff = len(df)
    raw_rm = (ctx.get("remaining_months") or "").strip()
    if raw_rm:
        try:
            _ = max(1, min(600, int(float(raw_rm))))
            has_stated_remaining = True
        except ValueError:
            has_stated_remaining = False
    else:
        has_stated_remaining = False
    yr_payoff = round(months_to_payoff / 12.0, 2) if months_to_payoff else 0.0
    if has_stated_remaining:
        payments_remaining_display = months_total
        if months_to_payoff != months_total:
            payments_remaining_sub = (
                f"Payoff in this model after {months_to_payoff} mo (~{yr_payoff} yr)"
            )
        else:
            payments_remaining_sub = f"~{yr_payoff} yr · matches stated term"
    else:
        payments_remaining_display = months_to_payoff
        payments_remaining_sub = f"~{yr_payoff} yr · Stated term {months_total} mo"
    summary = {
        "months": months_to_payoff,
        "term_months": months_total,
        "years_paid": round(months_to_payoff / 12.0, 2) if months_to_payoff else 0.0,
        "total_paid": total_paid,
        "payments_remaining_display": payments_remaining_display,
        "payments_remaining_sub": payments_remaining_sub,
    }
    total_paid_display = format_compact_amount(total_paid, currency)
    principal_compact = format_compact_amount(principal, currency)

    yearly_interest_rows = _yearly_interest_rows(df, currency)
    total_interest = float(df["Interest"].sum()) if len(df) else 0.0
    yearly_interest_total_display = format_compact_amount(total_interest, currency)

    return render_template(
        "index.html",
        chart_html=chart,
        table_html=table_html,
        yearly_interest_rows=yearly_interest_rows,
        yearly_interest_total_display=yearly_interest_total_display,
        summary=summary,
        principal_compact=principal_compact,
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
