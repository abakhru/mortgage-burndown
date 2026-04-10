"""Display metadata for common currencies (symbol + placement). Amounts are not converted."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Currency:
    code: str
    symbol: str
    label: str
    position: str  # "before" | "after"


# ISO-style codes; symbols are for display only (no FX conversion).
CURRENCIES: dict[str, Currency] = {
    "USD": Currency("USD", "$", "US dollar ($)", "before"),
    "INR": Currency("INR", "₹", "Indian rupee (₹)", "before"),
    "EUR": Currency("EUR", "€", "Euro (€)", "before"),
    "GBP": Currency("GBP", "£", "Pound sterling (£)", "before"),
    "JPY": Currency("JPY", "¥", "Japanese yen (¥)", "before"),
    "CNY": Currency("CNY", "¥", "Chinese yuan (¥)", "before"),
    "KRW": Currency("KRW", "₩", "South Korean won (₩)", "before"),
    "AUD": Currency("AUD", "A$", "Australian dollar (A$)", "before"),
    "CAD": Currency("CAD", "C$", "Canadian dollar (C$)", "before"),
    "SGD": Currency("SGD", "S$", "Singapore dollar (S$)", "before"),
    "HKD": Currency("HKD", "HK$", "Hong Kong dollar (HK$)", "before"),
    "NZD": Currency("NZD", "NZ$", "New Zealand dollar (NZ$)", "before"),
    "CHF": Currency("CHF", "CHF", "Swiss franc (CHF)", "after"),
    "SEK": Currency("SEK", "kr", "Swedish krona (kr)", "after"),
    "NOK": Currency("NOK", "kr", "Norwegian krone (kr)", "after"),
    "MXN": Currency("MXN", "MX$", "Mexican peso (MX$)", "before"),
    "BRL": Currency("BRL", "R$", "Brazilian real (R$)", "before"),
    "ZAR": Currency("ZAR", "R", "South African rand (R)", "before"),
    "TRY": Currency("TRY", "₺", "Turkish lira (₺)", "before"),
    "PLN": Currency("PLN", "zł", "Polish złoty (zł)", "after"),
    "THB": Currency("THB", "฿", "Thai baht (฿)", "before"),
    "AED": Currency("AED", "د.إ", "UAE dirham (د.إ)", "before"),
    "SAR": Currency("SAR", "﷼", "Saudi riyal (﷼)", "before"),
    "ILS": Currency("ILS", "₪", "Israeli shekel (₪)", "before"),
}


DEFAULT_CURRENCY_CODE = "INR"


def get_currency(code: str | None) -> Currency:
    if not code:
        return CURRENCIES[DEFAULT_CURRENCY_CODE]
    return CURRENCIES.get(code.upper(), CURRENCIES[DEFAULT_CURRENCY_CODE])


def format_money(amount: float, c: Currency) -> str:
    s = f"{amount:,.2f}"
    if c.position == "before":
        return f"{c.symbol}{s}"
    return f"{s} {c.symbol}"


def format_compact_amount(amount: float, c: Currency) -> str:
    """
    Human-scale amounts for UI: INR in Lac / Cr; USD in millions (from $100k up).
    Falls back to format_money for other currencies or smaller amounts.
    """
    a = abs(float(amount))
    sign = "-" if amount < 0 else ""

    if c.code == "INR":
        if a >= 1e7:
            val = a / 1e7
            core = f"{sign}{val:,.2f} Cr"
        elif a >= 1e5:
            val = a / 1e5
            core = f"{sign}{val:,.2f} Lac"
        else:
            return format_money(amount, c)
        return f"{c.symbol}{core}" if c.position == "before" else f"{core} {c.symbol}"

    if c.code == "USD":
        if a >= 1e5:
            m = a / 1e6
            core = f"{sign}{m:,.2f}M"
            return f"{c.symbol}{core}" if c.position == "before" else f"{core} {c.symbol}"
        return format_money(amount, c)

    return format_money(amount, c)
