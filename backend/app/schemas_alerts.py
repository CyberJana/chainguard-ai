from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AlertStatus = Literal["open", "acknowledged", "investigating", "dismissed", "closed"]
AlertSeverity = Literal["medium", "high", "critical"]


class AlertResponse(BaseModel):
    id: int
    transaction_id: int
    risk_assessment_id: int
    severity: AlertSeverity
    status: AlertStatus
    assigned_user_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AlertUpdate(BaseModel):
    status: AlertStatus | None = None
    assigned_user_id: int | None = Field(default=None, ge=1)

    @field_validator("status", mode="before")
    @classmethod
    def reject_null_status(cls, value: object) -> object:
        if value is None:
            raise ValueError("status cannot be null")
        return value
