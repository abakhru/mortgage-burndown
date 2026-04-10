# Mortgage burndown

Local web app to model a fixed-payment mortgage: remaining amortization, balance over time, optional prepayments and rate changes on specific dates, and calendar payment dates when you set the first EMI.

## Features

- **Amortization schedule** — principal, interest, balance per month; optional **prepayments** and **rate changes** tied to effective dates (mapped to payment months).
- **Loan in progress** — set **loan start date** (first EMI) to derive payments already made and current balance from the original principal, or model from disbursement only.
- **Charts & tables** — Plotly balance chart and HTML schedule; yearly interest rollups when dates are available.
- **Currency display** — symbol and formatting (including INR Lac/Cr and large USD shorthand). Amounts are **not** converted between currencies.

The underlying model keeps the **monthly payment fixed** from the first payment (initial principal, term, starting rate). Rate changes apply to **interest** only; if the rate rises enough that interest exceeds that payment, the balance can grow until a later change or prepayment (negative amortization).

## Requirements

- Python **3.12+**

## Run

```bash
uv run python -m mortgage_burndown
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000).

If you use [just](https://github.com/casey/just), `just run` (or `just dev`) runs the same command.

## Development

| Command      | Description                                      |
|-------------|---------------------------------------------------|
| `just install` | `uv sync` — install deps from lockfile         |
| `just run`     | Start the Flask app                             |
| `just lint`    | Ruff check                                      |
| `just fix`     | Ruff check with auto-fix                        |
| `just check`   | Byte-compile + import smoke test                |

## Layout

- `src/mortgage_burndown/` — Flask app (`app.py`), amortization math (`mortgage.py`), currency helpers (`currency.py`)
- `templates/` — Jinja templates
