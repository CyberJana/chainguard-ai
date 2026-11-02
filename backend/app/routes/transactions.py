import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import CurrentUser
from app.models import Alert, AuditLog, BlockchainTransaction, EthereumTransaction, RiskAssessment
from app.schemas import (
    BitcoinAddressHistoryResponse,
    EthereumAddressSnapshotResponse,
    EthereumTransactionResponse,
    RiskRequest,
    RiskResponse,
    TransactionResponse,
)
from app.services.bitcoin import fetch_bitcoin_address_history, ingest_bitcoin_transaction
from app.services.ethereum import (
    ADDRESS_PATTERN as ETHEREUM_ADDRESS_PATTERN,
    TX_HASH_PATTERN as ETHEREUM_TX_HASH_PATTERN,
    fetch_ethereum_address_snapshot,
    ingest_ethereum_transaction,
)
from app.services.risk import MODEL_VERSION, assess_bitcoin_transaction

router = APIRouter(prefix="/api/v1", tags=["blockchain"])
DbSession = Annotated[Session, Depends(get_db)]
TXID_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


@router.get("/transactions", response_model=list[TransactionResponse])
def list_transactions(
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[BlockchainTransaction]:
    del user
    return list(
        db.scalars(
            select(BlockchainTransaction)
            .order_by(BlockchainTransaction.updated_at.desc(), BlockchainTransaction.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/transactions/bitcoin/{txid}", response_model=TransactionResponse)
def lookup_bitcoin_transaction(txid: str, user: CurrentUser, db: DbSession) -> BlockchainTransaction:
    if not TXID_PATTERN.fullmatch(txid):
        raise HTTPException(status_code=422, detail="Invalid Bitcoin transaction ID")
    transaction = ingest_bitcoin_transaction(db, txid)
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            user_id=user.id,
            event_type="transaction.lookup",
            details=f"bitcoin:{transaction.txid}",
        )
    )
    db.commit()
    db.refresh(transaction)
    return transaction


@router.get("/transactions/ethereum/{tx_hash}", response_model=EthereumTransactionResponse)
def lookup_ethereum_transaction(
    tx_hash: str,
    user: CurrentUser,
    db: DbSession,
) -> EthereumTransaction:
    if not ETHEREUM_TX_HASH_PATTERN.fullmatch(tx_hash):
        raise HTTPException(status_code=422, detail="Invalid Ethereum transaction hash")
    transaction = ingest_ethereum_transaction(db, tx_hash)
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            user_id=user.id,
            event_type="transaction.lookup",
            details=f"ethereum:{transaction.chain_id}:{transaction.tx_hash}",
        )
    )
    db.commit()
    db.refresh(transaction)
    return transaction


@router.get("/addresses/ethereum/{address}", response_model=EthereumAddressSnapshotResponse)
def lookup_ethereum_address(
    address: str,
    user: CurrentUser,
    db: DbSession,
) -> EthereumAddressSnapshotResponse:
    if not ETHEREUM_ADDRESS_PATTERN.fullmatch(address):
        raise HTTPException(status_code=422, detail="Invalid Ethereum address")
    snapshot = fetch_ethereum_address_snapshot(address)
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            user_id=user.id,
            event_type="address.lookup",
            details=(
                f"ethereum:{snapshot['chain_id']}:{snapshot['address']}; "
                f"block={snapshot['block_number']}; source={snapshot['source']}"
            ),
        )
    )
    db.commit()
    return EthereumAddressSnapshotResponse.model_validate(snapshot)


@router.get("/addresses/bitcoin/{address}", response_model=BitcoinAddressHistoryResponse)
def lookup_bitcoin_address(
    address: str,
    user: CurrentUser,
    db: DbSession,
    cursor: Annotated[str | None, Query(pattern=r"^[0-9a-fA-F]{64}$")] = None,
) -> BitcoinAddressHistoryResponse:
    history = fetch_bitcoin_address_history(address, cursor)
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            user_id=user.id,
            event_type="address.lookup",
            details=f"bitcoin:{history.address}; source={history.source}",
        )
    )
    db.commit()
    return BitcoinAddressHistoryResponse.model_validate(asdict(history))


@router.post("/risk/analyze", response_model=RiskResponse)
def analyze_transaction(
    payload: RiskRequest,
    user: CurrentUser,
    db: DbSession,
) -> RiskAssessment:
    transaction = db.scalar(
        select(BlockchainTransaction).where(
            BlockchainTransaction.network == "bitcoin",
            BlockchainTransaction.txid == payload.txid.lower(),
        )
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction has not been ingested")

    result = assess_bitcoin_transaction(transaction.raw_observation)
    assessment = db.scalar(
        select(RiskAssessment).where(
            RiskAssessment.organization_id == user.organization_id,
            RiskAssessment.transaction_id == transaction.id,
            RiskAssessment.model_version == MODEL_VERSION,
        )
    )
    if assessment is None:
        assessment = RiskAssessment(
            organization_id=user.organization_id,
            transaction_id=transaction.id,
            score=result.score,
            category=result.category,
            model_version=MODEL_VERSION,
            calibrated=False,
            explanation=result.explanation,
            recommendation=result.recommendation,
        )
        db.add(assessment)
        db.flush()
    else:
        assessment.score = result.score
        assessment.category = result.category
        assessment.explanation = result.explanation
        assessment.recommendation = result.recommendation
        assessment.analyzed_at = datetime.now(timezone.utc)

    if result.score >= 20 and db.scalar(select(Alert.id).where(Alert.risk_assessment_id == assessment.id)) is None:
        db.add(
            Alert(
                organization_id=user.organization_id,
                transaction_id=transaction.id,
                risk_assessment_id=assessment.id,
                severity=result.category,
            )
        )
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            user_id=user.id,
            event_type="risk.analysis",
            details=f"bitcoin:{transaction.txid}; model={MODEL_VERSION}; score={result.score}",
        )
    )
    db.commit()
    db.refresh(assessment)
    return assessment
