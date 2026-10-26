"""Safe Harbor generalizations: ZIP to 3 digits, dates to year, ages over 89 to 90+."""

from __future__ import annotations

import datetime as dt
import re

DATE_PLACEHOLDER = "[DATE]"
AGE_90 = "90+"

MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
          "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
HOLIDAYS = (r"christmas(?:\s+(?:eve|day))?|xmas|new\s+year'?s(?:\s+(?:eve|day))?|thanksgiving|easter|halloween|"
            r"valentine'?s\s+day|memorial\s+day|labor\s+day|independence\s+day|fourth\s+of\s+july|july\s+4(?:th)?|"
            r"veterans\s+day|martin\s+luther\s+king\s+(?:jr\.?\s+)?day|mlk\s+day|presidents'?\s+day|"
            r"mother'?s\s+day|father'?s\s+day|juneteenth|columbus\s+day|cinco\s+de\s+mayo|st\.?\s+patrick'?s\s+day")

MONTH_RE = re.compile(rf"\b(?:{MONTHS})\b\.?", re.IGNORECASE)
HOLIDAY_RE = re.compile(rf"\b(?:{HOLIDAYS})\b", re.IGNORECASE)
YEAR4_RE = re.compile(r"(?<!\d)(1[6-9]\d\d|20\d\d|21\d\d)(?!\d)")   # 21xx: date-shifted research data
NUMERIC_DATE_RE = re.compile(r"(?<!\d)(\d{1,4})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?(?!\d)")
APOS_YEAR_RE = re.compile(r"'(\d\d)\b")
COMPACT_DATE_RE = re.compile(r"(?<!\d)((?:19|20|21)\d\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?:T?\d{4,6}Z?)?(?!\d)")


class Generalizer:
    def __init__(self, reference_year: int, zip3_restricted: set[str]):
        self.reference_year = reference_year
        self.zip3_restricted = zip3_restricted

    # ---- ZIP
    def zip3(self, value: str) -> str | None:
        digits = re.sub(r"\D", "", value)
        if len(digits) not in (3, 4, 5, 9):
            return None
        digits = digits.zfill(5) if len(digits) in (3, 4) else digits
        first3 = digits[:3]
        return "000" if first3 in self.zip3_restricted else first3

    def zip_text(self, value: str) -> str:
        z = self.zip3(value)
        return f"{z}XX" if z else "[ZIP]"

    # ---- dates
    def _expand_year(self, yy: int) -> int:
        return 2000 + yy if yy <= self.reference_year % 100 else 1900 + yy

    def year_of(self, text: str) -> int | None:
        m = COMPACT_DATE_RE.search(text)
        if m:
            return int(m.group(1))
        m = YEAR4_RE.search(text)
        if m:
            return int(m.group(1))
        m = NUMERIC_DATE_RE.search(text)
        if m and m.group(3) and len(m.group(3)) == 2:
            return self._expand_year(int(m.group(3)))
        m = APOS_YEAR_RE.search(text)
        if m:
            return self._expand_year(int(m.group(1)))
        return None

    @staticmethod
    def has_date_element(text: str) -> bool:
        """True if text carries a month, day or holiday (anything finer than a year)."""
        if MONTH_RE.search(text) or HOLIDAY_RE.search(text) or COMPACT_DATE_RE.search(text):
            return True
        m = NUMERIC_DATE_RE.search(text)
        if not m:
            return False
        a, b, c = m.group(1), m.group(2), m.group(3)
        if len(a) == 4:                       # 2021-03-14 or 2021-03
            return 1 <= int(b) <= 12
        if c is None:                         # 3/14 or 03/2021
            return 1 <= int(a) <= 12 and (1 <= int(b) <= 31)
        return 1 <= int(a) <= 31 and 1 <= int(b) <= 31

    def date_text(self, text: str, birth: bool = False) -> str:
        year = self.year_of(text)
        if year is None:
            return DATE_PLACEHOLDER
        if birth and self.reference_year - year > 89:
            return AGE_90
        return str(year)

    def date_value(self, value, birth: bool = False):
        """For typed spreadsheet cells (datetime/date) - returns int year or '90+'."""
        if isinstance(value, (dt.date, dt.datetime)):
            if birth and self.reference_year - value.year > 89:
                return AGE_90
            return value.year
        return self.date_text(str(value), birth=birth)

    # ---- ages
    def age(self, value) -> object:
        try:
            n = float(str(value).strip().rstrip("+"))
        except ValueError:
            return value
        return AGE_90 if n >= 90 else value
