from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import AvailabilityStatus


SHANGHAI = ZoneInfo("Asia/Shanghai")

# Private calendar blocks. A2A responses expose only availability, never labels.
WEEKLY_BUSY: dict[str, dict[int, list[tuple[time, time]]]] = {
    "opc-builder": {
        0: [(time(10, 0), time(11, 0)), (time(14, 0), time(14, 30))],
    },
    "shen-zhiye": {
        0: [(time(11, 0), time(12, 0)), (time(15, 30), time(16, 30))],
    },
    "lin-yu": {
        0: [(time(14, 30), time(16, 0))],
    },
    "zhou-yi": {
        0: [(time(10, 30), time(11, 30)), (time(16, 0), time(17, 0))],
    },
}


@dataclass(frozen=True)
class AvailabilityResult:
    status: AvailabilityStatus
    start: datetime
    end: datetime
    alternatives: list[datetime]

    @property
    def available(self) -> bool:
        return self.status == AvailabilityStatus.AVAILABLE


def check_availability(
    agent_id: str,
    requested_start: datetime,
    duration_minutes: int,
) -> AvailabilityResult:
    local_start = requested_start.astimezone(SHANGHAI)
    local_end = local_start + timedelta(minutes=duration_minutes)
    status = _status(agent_id, local_start, local_end)
    return AvailabilityResult(
        status=status,
        start=local_start,
        end=local_end,
        alternatives=(
            _alternatives(agent_id, local_start, duration_minutes)
            if status != AvailabilityStatus.AVAILABLE
            else []
        ),
    )


def _status(
    agent_id: str,
    local_start: datetime,
    local_end: datetime,
) -> AvailabilityStatus:
    if (
        local_start.weekday() >= 5
        or local_start.time() < time(9, 0)
        or local_end.time() > time(18, 0)
    ):
        return AvailabilityStatus.OUTSIDE_WORKING_HOURS

    busy_blocks = WEEKLY_BUSY.get(agent_id, {}).get(local_start.weekday(), [])
    overlaps = any(
        local_start.time() < busy_end and local_end.time() > busy_start
        for busy_start, busy_end in busy_blocks
    )
    return (
        AvailabilityStatus.BUSY if overlaps else AvailabilityStatus.AVAILABLE
    )


def _alternatives(
    agent_id: str,
    requested_start: datetime,
    duration_minutes: int,
) -> list[datetime]:
    alternatives: list[datetime] = []
    candidate = requested_start.replace(hour=9, minute=0, second=0, microsecond=0)
    while candidate.hour < 18 and len(alternatives) < 3:
        if candidate != requested_start:
            candidate_end = candidate + timedelta(minutes=duration_minutes)
            if _status(agent_id, candidate, candidate_end) == AvailabilityStatus.AVAILABLE:
                alternatives.append(candidate)
        candidate += timedelta(minutes=30)
    return alternatives
