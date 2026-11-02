import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import EthereumTransaction, EthereumTransactionObservation

TX_HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
QUANTITY_PATTERN = re.compile(r"^0x[0-9a-fA-F]+$")
MAX_UINT256 = (1 << 256) - 1
MAX_DATABASE_INTEGER = (1 << 63) - 1
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024


def _quantity(value: Any, field: str, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or len(value) > 66 or not QUANTITY_PATTERN.fullmatch(value):
        raise ValueError(f"Ethereum provider {field} is invalid.")
    parsed = int(value, 16)
    if parsed > MAX_UINT256:
        raise ValueError(f"Ethereum provider {field} exceeds uint256.")
    return parsed


def normalize_ethereum_transaction(tx_hash: str, transaction: Any, receipt: Any, chain_id: Any) -> dict[str, Any]:
    if not TX_HASH_PATTERN.fullmatch(tx_hash):
        raise ValueError("Invalid Ethereum transaction hash.")
    if (
        not isinstance(transaction, dict)
        or not isinstance(transaction.get("hash"), str)
        or transaction["hash"].lower() != tx_hash.lower()
    ):
        raise ValueError("Ethereum provider transaction hash did not match the request.")
    from_address = transaction.get("from")
    if not isinstance(from_address, str) or not ADDRESS_PATTERN.fullmatch(from_address):
        raise ValueError("Ethereum provider sender address is invalid.")
    to_address = transaction.get("to")
    if to_address is not None and (
        not isinstance(to_address, str) or not ADDRESS_PATTERN.fullmatch(to_address)
    ):
        raise ValueError("Ethereum provider recipient address is invalid.")

    parsed_chain_id = _quantity(chain_id, "chain ID")
    gas_limit = _quantity(transaction.get("gas"), "gas limit")
    block_number = _quantity(transaction.get("blockNumber"), "block number", optional=True)
    value = _quantity(transaction.get("value"), "value")
    if parsed_chain_id is None or parsed_chain_id > MAX_DATABASE_INTEGER:
        raise ValueError("Ethereum provider chain ID is outside the supported range.")
    if gas_limit is None or gas_limit > MAX_DATABASE_INTEGER:
        raise ValueError("Ethereum provider gas limit is outside the supported range.")
    if block_number is not None and block_number > MAX_DATABASE_INTEGER:
        raise ValueError("Ethereum provider block number is outside the supported range.")
    if value is None:
        raise ValueError("Ethereum provider value is missing.")

    block_hash = transaction.get("blockHash")
    if block_hash is not None and (
        not isinstance(block_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", block_hash)
    ):
        raise ValueError("Ethereum provider block hash is invalid.")
    if receipt is not None:
        if (
            not isinstance(receipt, dict)
            or not isinstance(receipt.get("transactionHash"), str)
            or receipt["transactionHash"].lower() != tx_hash.lower()
        ):
            raise ValueError("Ethereum provider receipt did not match the transaction.")
        receipt_status_value = _quantity(receipt.get("status"), "receipt status", optional=True)
        if receipt_status_value not in (0, 1):
            raise ValueError("Ethereum provider receipt status must be zero or one.")
        receipt_block_number = _quantity(receipt.get("blockNumber"), "receipt block number", optional=True)
        receipt_block_hash = receipt.get("blockHash")
        if receipt_block_hash is not None and (
            not isinstance(receipt_block_hash, str)
            or not re.fullmatch(r"0x[0-9a-fA-F]{64}", receipt_block_hash)
        ):
            raise ValueError("Ethereum provider receipt block hash is invalid.")
        if (
            block_number is not None
            and receipt_block_number is not None
            and block_number != receipt_block_number
        ):
            raise ValueError("Ethereum provider transaction and receipt block numbers disagree.")
        if (
            block_hash is not None
            and receipt_block_hash is not None
            and block_hash.lower() != receipt_block_hash.lower()
        ):
            raise ValueError("Ethereum provider transaction and receipt block hashes disagree.")
        block_number = block_number if block_number is not None else receipt_block_number
        block_hash = block_hash or receipt_block_hash
        gas_used = _quantity(receipt.get("gasUsed"), "gas used")
        effective_gas_price = _quantity(
            receipt.get("effectiveGasPrice"), "effective gas price", optional=True
        )
        if gas_used is None or gas_used > MAX_DATABASE_INTEGER:
            raise ValueError("Ethereum provider gas used is outside the supported range.")
        receipt_status = "success" if receipt_status_value == 1 else "reverted"
    else:
        gas_used = None
        effective_gas_price = None
        receipt_status = None
    if block_number is not None and block_number > MAX_DATABASE_INTEGER:
        raise ValueError("Ethereum provider block number is outside the supported range.")

    return {
        "chain_id": parsed_chain_id,
        "tx_hash": tx_hash.lower(),
        "block_number": block_number,
        "block_hash": block_hash.lower() if block_hash else None,
        "receipt_status": receipt_status,
        "from_address": from_address.lower(),
        "to_address": to_address.lower() if to_address else None,
        "value_wei": str(value),
        "gas_limit": gas_limit,
        "gas_used": gas_used,
        "effective_gas_price_wei": str(effective_gas_price) if effective_gas_price is not None else None,
    }


def _rpc_request(client: httpx.Client, provider_url: str, method: str, params: list[Any]) -> Any:
    try:
        with client.stream(
            "POST",
            provider_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"Accept": "application/json"},
        ) as response:
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="Configured Ethereum provider returned an error")
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_PROVIDER_RESPONSE_BYTES:
                        raise HTTPException(status_code=502, detail="Configured Ethereum provider response was too large")
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail="Configured Ethereum provider returned invalid headers") from exc
            chunks: list[bytes] = []
            total_size = 0
            for chunk in response.iter_bytes():
                total_size += len(chunk)
                if total_size > MAX_PROVIDER_RESPONSE_BYTES:
                    raise HTTPException(status_code=502, detail="Configured Ethereum provider response was too large")
                chunks.append(chunk)
    except HTTPException:
        raise
    except httpx.TransportError as exc:
        raise HTTPException(status_code=502, detail="Configured Ethereum provider is unavailable") from exc

    try:
        result = json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Configured Ethereum provider returned invalid JSON") from exc
    if (
        not isinstance(result, dict)
        or result.get("jsonrpc") != "2.0"
        or type(result.get("id")) is not int
        or result.get("id") != 1
    ):
        raise HTTPException(status_code=502, detail="Configured Ethereum provider returned an invalid RPC response")
    if result.get("error") is not None:
        raise HTTPException(status_code=502, detail="Configured Ethereum provider reported an RPC error")
    if "result" not in result:
        raise HTTPException(status_code=502, detail="Configured Ethereum provider omitted the RPC result")
    return result["result"]


def fetch_ethereum_transaction(tx_hash: str) -> tuple[dict[str, Any], str]:
    if not TX_HASH_PATTERN.fullmatch(tx_hash):
        raise HTTPException(status_code=422, detail="Invalid Ethereum transaction hash")
    if settings.ethereum_provider_url is None:
        raise HTTPException(status_code=503, detail="Ethereum lookup is not configured")
    provider_url = settings.ethereum_provider_url

    try:
        with httpx.Client(
            timeout=settings.ethereum_provider_timeout_seconds,
            follow_redirects=False,
        ) as client:
            chain_id = _rpc_request(client, provider_url, "eth_chainId", [])
            transaction = _rpc_request(client, provider_url, "eth_getTransactionByHash", [tx_hash.lower()])
            if transaction is None:
                raise HTTPException(status_code=404, detail="Ethereum transaction not found by configured provider")
            receipt = _rpc_request(client, provider_url, "eth_getTransactionReceipt", [tx_hash.lower()])
    except HTTPException:
        raise
    except httpx.TransportError as exc:
        raise HTTPException(status_code=502, detail="Configured Ethereum provider is unavailable") from exc

    try:
        normalize_ethereum_transaction(tx_hash, transaction, receipt, chain_id)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(status_code=502, detail="Configured Ethereum provider returned invalid transaction data") from exc
    payload = {"chain_id": chain_id, "transaction": transaction, "receipt": receipt}
    hostname = urlparse(provider_url).hostname or "configured-provider"
    return payload, hostname


def fetch_ethereum_address_snapshot(address: str) -> dict[str, Any]:
    if not isinstance(address, str) or not ADDRESS_PATTERN.fullmatch(address):
        raise HTTPException(status_code=422, detail="Invalid Ethereum address")
    if settings.ethereum_provider_url is None:
        raise HTTPException(status_code=503, detail="Ethereum lookup is not configured")
    provider_url = settings.ethereum_provider_url

    try:
        with httpx.Client(
            timeout=settings.ethereum_provider_timeout_seconds,
            follow_redirects=False,
        ) as client:
            chain_id = _quantity(_rpc_request(client, provider_url, "eth_chainId", []), "chain ID")
            latest_block = _rpc_request(client, provider_url, "eth_getBlockByNumber", ["latest", False])
            if not isinstance(latest_block, dict):
                raise HTTPException(status_code=502, detail="Ethereum provider returned an invalid latest block")
            block_number = _quantity(latest_block.get("number"), "latest block number")
            block_hash = latest_block.get("hash")
            if (
                block_number is None
                or block_number > MAX_DATABASE_INTEGER
                or not isinstance(block_hash, str)
                or not re.fullmatch(r"0x[0-9a-fA-F]{64}", block_hash)
            ):
                raise HTTPException(status_code=502, detail="Ethereum provider returned an invalid latest block")
            block_tag = hex(block_number)
            balance = _quantity(
                _rpc_request(client, provider_url, "eth_getBalance", [address.lower(), block_tag]),
                "account balance",
            )
            transaction_count = _quantity(
                _rpc_request(client, provider_url, "eth_getTransactionCount", [address.lower(), block_tag]),
                "account transaction count",
            )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Configured Ethereum provider returned invalid address data") from exc
    except httpx.TransportError as exc:
        raise HTTPException(status_code=502, detail="Configured Ethereum provider is unavailable") from exc

    if chain_id is None or chain_id > MAX_DATABASE_INTEGER or balance is None or transaction_count is None:
        raise HTTPException(status_code=502, detail="Configured Ethereum provider returned invalid address data")
    if transaction_count > MAX_DATABASE_INTEGER:
        raise HTTPException(status_code=502, detail="Ethereum account transaction count is outside the supported range")
    return {
        "chain_id": chain_id,
        "address": address.lower(),
        "balance_wei": str(balance),
        "transaction_count": transaction_count,
        "block_number": block_number,
        "block_hash": block_hash.lower(),
        "source": urlparse(provider_url).hostname or "configured-provider",
        "retrieved_at": datetime.now(timezone.utc),
    }


def ingest_ethereum_transaction(db: Session, tx_hash: str) -> EthereumTransaction:
    payload, source = fetch_ethereum_transaction(tx_hash)
    normalized = normalize_ethereum_transaction(
        tx_hash,
        payload["transaction"],
        payload["receipt"],
        payload["chain_id"],
    )
    observed_at = datetime.now(timezone.utc)
    transaction = db.scalar(
        select(EthereumTransaction).where(
            EthereumTransaction.chain_id == normalized["chain_id"],
            EthereumTransaction.tx_hash == normalized["tx_hash"],
        )
    )
    if transaction is None:
        transaction = EthereumTransaction(
            **normalized,
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
                select(EthereumTransaction).where(
                    EthereumTransaction.chain_id == normalized["chain_id"],
                    EthereumTransaction.tx_hash == normalized["tx_hash"],
                )
            )
            if transaction is None:
                raise

    for field, value in normalized.items():
        setattr(transaction, field, value)
    transaction.source = source
    transaction.raw_observation = payload
    transaction.observed_at = observed_at
    transaction.updated_at = observed_at

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    observation_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    existing = db.scalar(
        select(EthereumTransactionObservation.id).where(
            EthereumTransactionObservation.transaction_id == transaction.id,
            EthereumTransactionObservation.observation_hash == observation_hash,
        )
    )
    if existing is None:
        db.add(
            EthereumTransactionObservation(
                transaction_id=transaction.id,
                observation_hash=observation_hash,
                provider=source,
                payload=payload,
                observed_at=observed_at,
            )
        )
    db.flush()
    return transaction
