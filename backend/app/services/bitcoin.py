import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BlockchainTransaction, TransactionObservation

TXID_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class NormalizedBitcoinTransaction:
    txid: str
    block_height: int | None
    block_hash: str | None
    block_time: datetime | None
    is_confirmed: bool | None
    fee_satoshis: int
    input_count: int
    output_count: int
    output_total_satoshis: int


def normalize_bitcoin_transaction(txid: str, payload: Any) -> NormalizedBitcoinTransaction:
    if not isinstance(payload, dict) or not isinstance(payload.get("txid"), str) or payload["txid"].lower() != txid.lower():
        raise ValueError("Provider transaction identifier did not match the requested transaction.")
    inputs = payload.get("vin")
    outputs = payload.get("vout")
    status = payload.get("status", {})
    if not isinstance(inputs, list) or not isinstance(outputs, list) or not isinstance(status, dict):
        raise ValueError("Provider transaction response has an invalid shape.")

    fee = payload.get("fee")
    if type(fee) is not int or fee < 0:
        raise ValueError("Provider transaction fee is invalid.")
    total = 0
    for output in outputs:
        if not isinstance(output, dict):
            raise ValueError("Provider transaction output is invalid.")
        value = output.get("value")
        if type(value) is not int or value < 0:
            raise ValueError("Provider transaction output value is invalid.")
        total += value

    block_height = status.get("block_height")
    if block_height is not None and (type(block_height) is not int or block_height < 0):
        raise ValueError("Provider block height is invalid.")
    block_hash = status.get("block_hash")
    if block_hash is not None and not isinstance(block_hash, str):
        raise ValueError("Provider block hash is invalid.")
    block_time = status.get("block_time")
    normalized_time = None
    if block_time is not None:
        if type(block_time) is not int or block_time < 0:
            raise ValueError("Provider block time is invalid.")
        normalized_time = datetime.fromtimestamp(block_time, tz=timezone.utc)
    confirmed = status.get("confirmed")
    if confirmed is not None and type(confirmed) is not bool:
        raise ValueError("Provider confirmation status is invalid.")
    if confirmed is None and block_height is not None:
        confirmed = True

    return NormalizedBitcoinTransaction(
        txid=txid.lower(),
        block_height=block_height,
        block_hash=block_hash,
        block_time=normalized_time,
        is_confirmed=confirmed,
        fee_satoshis=fee,
        input_count=len(inputs),
        output_count=len(outputs),
        output_total_satoshis=total,
    )


def fetch_bitcoin_transaction(txid: str) -> tuple[dict, str]:
    if not TXID_PATTERN.fullmatch(txid):
        raise HTTPException(status_code=422, detail="Invalid Bitcoin transaction ID")

    url = f"{settings.bitcoin_provider_base_url}/tx/{quote(txid.lower(), safe='')}"
    try:
        with httpx.Client(timeout=settings.bitcoin_provider_timeout_seconds, follow_redirects=False) as client:
            with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                if response.status_code == 404:
                    raise HTTPException(status_code=404, detail="Transaction not found by configured provider")
                if response.status_code != 200:
                    raise HTTPException(status_code=502, detail="Configured blockchain provider returned an error")
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > MAX_PROVIDER_RESPONSE_BYTES:
                            raise HTTPException(status_code=502, detail="Configured blockchain provider response was too large")
                    except ValueError as exc:
                        raise HTTPException(status_code=502, detail="Configured blockchain provider returned invalid headers") from exc
                chunks: list[bytes] = []
                total_size = 0
                for chunk in response.iter_bytes():
                    total_size += len(chunk)
                    if total_size > MAX_PROVIDER_RESPONSE_BYTES:
                        raise HTTPException(status_code=502, detail="Configured blockchain provider response was too large")
                    chunks.append(chunk)
    except HTTPException:
        raise
    except httpx.TransportError as exc:
        raise HTTPException(status_code=502, detail="Configured blockchain provider is unavailable") from exc

    try:
        payload = json.loads(b"".join(chunks))
        normalize_bitcoin_transaction(txid, payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=502, detail="Configured blockchain provider returned invalid transaction data") from exc

    hostname = urlparse(settings.bitcoin_provider_base_url).hostname or "configured-provider"
    return payload, hostname


def ingest_bitcoin_transaction(db: Session, txid: str) -> BlockchainTransaction:
    payload, source = fetch_bitcoin_transaction(txid)
    normalized = normalize_bitcoin_transaction(txid, payload)
    observed_at = datetime.now(timezone.utc)
    transaction = db.scalar(
        select(BlockchainTransaction).where(
            BlockchainTransaction.network == "bitcoin",
            BlockchainTransaction.txid == normalized.txid,
        )
    )
    if transaction is None:
        transaction = BlockchainTransaction(
            network="bitcoin",
            txid=normalized.txid,
            source=source,
            raw_observation=payload,
            observed_at=observed_at,
        )
        db.add(transaction)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            transaction = db.scalar(
                select(BlockchainTransaction).where(
                    BlockchainTransaction.network == "bitcoin",
                    BlockchainTransaction.txid == normalized.txid,
                )
            )
            if transaction is None:
                raise

    transaction.block_height = normalized.block_height
    transaction.block_hash = normalized.block_hash
    transaction.block_time = normalized.block_time
    transaction.is_confirmed = normalized.is_confirmed
    transaction.confirmations = None
    transaction.fee_satoshis = normalized.fee_satoshis
    transaction.input_count = normalized.input_count
    transaction.output_count = normalized.output_count
    transaction.output_total_satoshis = normalized.output_total_satoshis
    transaction.source = source
    transaction.raw_observation = payload
    transaction.observed_at = observed_at
    transaction.updated_at = observed_at

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    observation_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    existing_observation = db.scalar(
        select(TransactionObservation.id).where(
            TransactionObservation.transaction_id == transaction.id,
            TransactionObservation.observation_hash == observation_hash,
        )
    )
    if existing_observation is None:
        db.add(
            TransactionObservation(
                transaction_id=transaction.id,
                observation_hash=observation_hash,
                provider=source,
                payload=payload,
                observed_at=observed_at,
            )
        )
    db.flush()
    return transaction
