"""Měsíční aritmetika na jednom místě.

`date` neumí „o měsíc dál" a každé místo, které to potřebovalo, si to dřív spočítalo
vlastním trikem (`replace(day=1) - 1 den`, `replace(day=28) + 7 dní`,
`year + month // 12, month % 12 + 1`). Všechny tři byly správné a žádný nebyl čitelný.

Měsíc je pár `(year, month)`, protože právě takhle je pojmenovaná partition. Porovnávat
se dá přímo (`(2024, 12) < (2025, 1)`), takže rozsah je obyčejný `while`.
"""

from __future__ import annotations


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def parse(value: str) -> tuple[int, int]:
    """`"2025-01"` -> `(2025, 1)`."""
    year, month = value.split("-")
    return int(year), int(month)


def label(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"
