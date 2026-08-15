from __future__ import annotations

import asyncio

from datetime import datetime, timezone
from uuid import uuid4

from .models import (
    MatchReport,
    ScreeningRecord,
    ScreeningState,
    TranscriptTurn,
)


class ScreeningStore:
    def __init__(self) -> None:
        self._records: dict[str, ScreeningRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        from_agent_id: str,
        to_agent_id: str,
        screening_id: str | None = None,
    ) -> ScreeningRecord:
        record = ScreeningRecord(
            id=screening_id or str(uuid4()),
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            state=ScreeningState.CREATED,
        )
        async with self._lock:
            self._records[record.id] = record
        return record.model_copy(deep=True)

    async def get(self, screening_id: str) -> ScreeningRecord:
        async with self._lock:
            record = self._records.get(screening_id)
            if record is None:
                raise KeyError(f"Unknown screening: {screening_id}")
            return record.model_copy(deep=True)

    async def set_state(
        self,
        screening_id: str,
        state: ScreeningState,
        error: str | None = None,
    ) -> ScreeningRecord:
        async with self._lock:
            record = self._records[screening_id]
            record.state = state
            record.error = error
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def add_turn(
        self,
        screening_id: str,
        turn: TranscriptTurn,
    ) -> None:
        async with self._lock:
            record = self._records[screening_id]
            record.transcript.append(turn)
            record.updated_at = datetime.now(timezone.utc)

    async def set_report(
        self,
        screening_id: str,
        report: MatchReport,
    ) -> None:
        async with self._lock:
            record = self._records[screening_id]
            record.report = report
            record.updated_at = datetime.now(timezone.utc)

    async def decide(
        self,
        screening_id: str,
        agent_id: str,
        decision: str,
    ) -> ScreeningRecord:
        async with self._lock:
            record = self._records[screening_id]
            if agent_id not in {record.from_agent_id, record.to_agent_id}:
                raise ValueError("Only a screening participant can decide")
            if decision == "decline":
                record.state = ScreeningState.DECLINED
            else:
                if agent_id not in record.approvals:
                    record.approvals.append(agent_id)
                record.state = (
                    ScreeningState.MUTUAL_APPROVED
                    if len(record.approvals) == 2
                    else ScreeningState.WAITING_REMOTE_APPROVAL
                )
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)
