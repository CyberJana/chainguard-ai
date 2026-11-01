from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CaseStatus = Literal["open", "in_review", "closed"]


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    assigned_user_id: int | None = Field(default=None, ge=1)


class CaseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    status: CaseStatus | None = None
    assigned_user_id: int | None = Field(default=None, ge=1)


class CaseResponse(BaseModel):
    id: int
    title: str
    description: str | None
    status: CaseStatus
    created_by_user_id: int
    assigned_user_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CaseTransactionRequest(BaseModel):
    transaction_id: int = Field(ge=1)


class NoteCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
