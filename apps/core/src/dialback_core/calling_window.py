"""When the core may place a call: weekdays 9:00 to 16:30 in the vendor's time zone (design,
"How a call runs"). Times come from the dataset's clock, so the lab's simulated time applies.
Configurable for the evening demo (spec 0002, Q7)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _days(spec: str) -> frozenset[int]:
    spec = spec.strip().lower()
    if spec in ("all", "*"):
        return frozenset(range(7))
    if "-" in spec:
        a, b = (DAYS.index(x.strip()) for x in spec.split("-", 1))
        return frozenset(range(a, b + 1))
    return frozenset(DAYS.index(x.strip()) for x in spec.split(","))


@dataclass(frozen=True)
class CallingWindow:
    tz: ZoneInfo
    start: dt.time
    end: dt.time
    days: frozenset[int]

    @classmethod
    def parse(cls, tz: str, window: str, days: str) -> CallingWindow:
        a, b = (dt.time.fromisoformat(x.strip()) for x in window.split("-", 1))
        if not a < b:
            raise ValueError("the calling window must start before it ends")
        return cls(ZoneInfo(tz), a, b, _days(days))

    def contains(self, now: dt.datetime) -> bool:
        local = now.astimezone(self.tz)
        return local.weekday() in self.days and self.start <= local.time() < self.end

    def next_open(self, now: dt.datetime) -> dt.datetime:
        """The next moment the window is open (now, if it already is)."""
        if self.contains(now):
            return now
        local = now.astimezone(self.tz)
        for offset in range(8):
            day = local.date() + dt.timedelta(days=offset)
            opens = dt.datetime.combine(day, self.start, tzinfo=self.tz)
            if day.weekday() in self.days and opens > local:
                return opens.astimezone(dt.UTC)
        raise ValueError("the calling window has no open day")
