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
BITCOIN_ADDRESS_PATTERN = re.compile(
    r"^(?:[13][1-9A-HJ-NP-Za-km-z]{25,34}|bc1[ac-hj-np-z02-9]{11,71})$"
)
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
ADDRESS_HISTORY_PAGE_SIZE = 25


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


@dataclass(frozen=True)
class BitcoinAddressStats:
    funded_txo_count: int
    funded_txo_sum: int
    spent_txo_count: int
    spent_txo_sum: int
    tx_count: int


@dataclass(frozen=True)
class BitcoinAddressHistory:
    address: str
    chain_stats: BitcoinAddressStats
    mempool_stats: BitcoinAddressStats
    transactions: tuple[NormalizedBitcoinTransaction, ...]
    next_cursor: str | None
    source: str
    retrieved_at: datetime


def _normalize_address_stats(value: Any) -> BitcoinAddressStats:
    if not isinstance(value, dict):
        raise ValueError("Provider address statistics have an invalid shape.")
    fields = ("funded_txo_count", "funded_txo_sum", "spent_txo_count", "spent_txo_sum", "tx_count")
    if any(type(value.get(field)) is not int or value[field] < 0 for field in fields):
        raise ValueError("Provider address statistics are invalid.")
    return BitcoinAddressStats(**{field: value[field] for field in fields})


def normalize_bitcoin_address_history(
    address: str,
    summary: Any,
    transactions: Any,
) -> tuple[BitcoinAddressStats, BitcoinAddressStats, tuple[NormalizedBitcoinTransaction, ...]]:
    if not isinstance(summary, dict) or summary.get("address") != address:
        raise ValueError("Provider address did not match the requested address.")
    chain_stats = _normalize_address_stats(summary.get("chain_stats"))
    mempool_stats = _normalize_address_stats(summary.get("mempool_stats"))
    if not isinstance(transactions, list) or len(transactions) > ADDRESS_HISTORY_PAGE_SIZE:
        raise ValueError("Provider address history has an invalid shape.")

    normalized_transactions = []
    seen_txids: set[str] = set()
    for payload in transactions:
        if not isinstance(payload, dict) or not isinstance(payload.get("txid"), str):
            raise ValueError("Provider address history contains an invalid transaction.")
        txid = payload["txid"]
        if not TXID_PATTERN.fullmatch(txid) or txid.lower() in seen_txids:
            raise ValueError("Provider address history contains an invalid or duplicate transaction.")
        inputs = payload.get("vin")
        outputs = payload.get("vout")
        if not isinstance(inputs, list) or not isinstance(outputs, list):
            raise ValueError("Provider address history contains an invalid transaction.")
        references_address = any(
            isinstance(item, dict)
            and isinstance(item.get("prevout"), dict)
            and item["prevout"].get("scriptpubkey_address") == address
            for item in inputs
        ) or any(
            isinstance(item, dict) and item.get("scriptpubkey_address") == address
            for item in outputs
        )
        if not references_address:
            raise ValueError("Provider address history contains a transaction unrelated to the requested address.")
        normalized_transactions.append(normalize_bitcoin_transaction(txid, payload))
        seen_txids.add(txid.lower())
    return chain_stats, mempool_stats, tuple(normalized_transactions)


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


def fetch_bitcoin_address_history(address: str, cursor: str | None = None) -> BitcoinAddressHistory:
    if not BITCOIN_ADDRESS_PATTERN.fullmatch(address):
        raise HTTPException(status_code=422, detail="Invalid Bitcoin address")
    if cursor is not None and not TXID_PATTERN.fullmatch(cursor):
        raise HTTPException(status_code=422, detail="Invalid address history cursor")

    encoded_address = quote(address, safe="")
    summary_url = f"{settings.bitcoin_provider_base_url}/address/{encoded_address}"
    history_path = f"/address/{encoded_address}/txs"
    if cursor is not None:
        history_path += f"/chain/{quote(cursor.lower(), safe='')}"

    def request_json(url: str, *, missing_detail: str, invalid_detail: str | None = None) -> Any:
        try:
            with httpx.Client(timeout=settings.bitcoin_provider_timeout_seconds, follow_redirects=False) as client:
                with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                    if response.status_code == 404:
                        raise HTTPException(status_code=404, detail=missing_detail)
                    if response.status_code == 400 and invalid_detail is not None:
                        raise HTTPException(status_code=422, detail=invalid_detail)
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
            return json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=502, detail="Configured blockchain provider returned invalid data") from exc

    summary = request_json(
        summary_url,
        missing_detail="Bitcoin address not found by configured provider",
        invalid_detail="Invalid Bitcoin address",
    )
    transactions = request_json(
        f"{settings.bitcoin_provider_base_url}{history_path}",
        missing_detail="Bitcoin address history not found by configured provider",
    )
    try:
        chain_stats, mempool_stats, normalized_transactions = normalize_bitcoin_address_history(
            address, summary, transactions
        )
    except (ValueError, OverflowError) as exc:
        raise HTTPException(status_code=502, detail="Configured blockchain provider returned invalid address data") from exc
    if cursor is not None and any(item.txid == cursor.lower() for item in normalized_transactions):
        raise HTTPException(status_code=502, detail="Configured blockchain provider returned a repeated address history page")

    hostname = urlparse(settings.bitcoin_provider_base_url).hostname or "configured-provider"
    next_cursor = (
        normalized_transactions[-1].txid
        if len(normalized_transactions) == ADDRESS_HISTORY_PAGE_SIZE
        else None
    )
    return BitcoinAddressHistory(
        address=address,
        chain_stats=chain_stats,
        mempool_stats=mempool_stats,
        transactions=normalized_transactions,
        next_cursor=next_cursor,
        source=hostname,
        retrieved_at=datetime.now(timezone.utc),
    )


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
