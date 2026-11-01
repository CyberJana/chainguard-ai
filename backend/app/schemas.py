from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: int
    organization_id: int
    email: EmailStr
    role: Role

    model_config = ConfigDict(from_attributes=True)


class TransactionResponse(BaseModel):
    id: int
    network: str
    txid: str
    block_height: int | None
    block_hash: str | None
    block_time: datetime | None
    is_confirmed: bool | None
    confirmations: int | None
    fee_satoshis: int | None
    input_count: int
    output_count: int
    output_total_satoshis: int
    raw_observation: dict[str, Any]
    source: str
    observed_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EthereumTransactionResponse(BaseModel):
    id: int
    chain_id: int
    tx_hash: str
    block_number: int | None
    block_hash: str | None
    receipt_status: str | None
    from_address: str
    to_address: str | None
    value_wei: str
    gas_limit: int
    gas_used: int | None
    effective_gas_price_wei: str | None
    raw_observation: dict[str, Any]
    source: str
    observed_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BitcoinAddressStatsResponse(BaseModel):
    funded_txo_count: int
    funded_txo_sum: int
    spent_txo_count: int
    spent_txo_sum: int
    tx_count: int


class BitcoinAddressTransactionResponse(BaseModel):
    txid: str
    block_height: int | None
    block_hash: str | None
    block_time: datetime | None
    is_confirmed: bool | None
    fee_satoshis: int
    input_count: int
    output_count: int
    output_total_satoshis: int


class BitcoinAddressHistoryResponse(BaseModel):
    address: str
    chain_stats: BitcoinAddressStatsResponse
    mempool_stats: BitcoinAddressStatsResponse
    transactions: list[BitcoinAddressTransactionResponse]
    next_cursor: str | None
    source: str
    retrieved_at: datetime


class RiskResponse(BaseModel):
    id: int
    transaction_id: int
    score: int
    category: str
    model_version: str
    calibrated: bool
    explanation: list[dict[str, Any]]
    recommendation: str
    analyzed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RiskRequest(BaseModel):
    txid: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=14, max_length=1024)
    role: Role


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None

    @field_validator("role", "is_active", mode="before")
    @classmethod
    def reject_null_updates(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class InvitationCreate(BaseModel):
    email: EmailStr
    role: Role


class InvitationResponse(BaseModel):
    invitation_token: str
    expires_at: datetime


class InvitationAccept(BaseModel):
    invitation_token: str = Field(min_length=32, max_length=128)
    password: str = Field(min_length=14, max_length=1024)
