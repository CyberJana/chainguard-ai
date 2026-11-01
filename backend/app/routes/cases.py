import csv
import hashlib
import io
import json
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import CurrentUser
from app.models import (
    AnalystNote,
    AuditLog,
    BlockchainTransaction,
    CaseEvent,
    CaseTransaction,
    EvidenceCustodyEvent,
    EvidenceItem,
    InvestigationCase,
    User,
)
from app.schemas_cases import (
    CaseCreate,
    CaseGraphResponse,
    CaseResponse,
    CaseTransactionRequest,
    CaseUpdate,
    NoteCreate,
)

router = APIRouter(prefix="/api/v1/cases", tags=["investigations"])
DbSession = Annotated[Session, Depends(get_db)]
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
SAFE_FILENAME = re.compile(r"[\x00-\x1f\x7f]")
CSV_FORMULA_PREFIX = re.compile(r"^\s*[=+\-@]")
CASE_REPORT_CSV_COLUMNS = (
    "record_type",
    "case_id",
    "case_title",
    "case_description",
    "case_status",
    "case_created_at",
    "methodology",
    "findings",
    "limitations",
    "network",
    "txid",
    "source",
    "observed_at",
    "raw_observation",
    "user_id",
    "content",
    "filename",
    "sha256",
    "byte_size",
    "source_provenance",
    "event_type",
    "details",
    "evidence_id",
    "action",
)
MAX_GRAPH_TRANSACTIONS = 25
MAX_GRAPH_NODES = 250
MAX_GRAPH_EDGES = 500


def get_case(db: Session, case_id: int, organization_id: int) -> InvestigationCase:
    case = db.scalar(
        select(InvestigationCase).where(
            InvestigationCase.id == case_id,
            InvestigationCase.organization_id == organization_id,
        )
    )
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def validate_assignee(db: Session, organization_id: int, user_id: int | None) -> None:
    if user_id is None:
        return
    assignee = db.scalar(
        select(User).where(
            User.id == user_id,
            User.organization_id == organization_id,
            User.is_active.is_(True),
        )
    )
    if assignee is None:
        raise HTTPException(status_code=422, detail="Assignee must be active and belong to this organization")


@router.get("", response_model=list[CaseResponse])
def list_cases(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[str | None, Query(alias="status", pattern="^(open|in_review|closed)$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[InvestigationCase]:
    statement = select(InvestigationCase).where(InvestigationCase.organization_id == user.organization_id)
    if status_filter:
        statement = statement.where(InvestigationCase.status == status_filter)
    statement = statement.order_by(InvestigationCase.updated_at.desc(), InvestigationCase.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(statement))


@router.post("", response_model=CaseResponse, status_code=201)
def create_case(payload: CaseCreate, user: CurrentUser, db: DbSession) -> InvestigationCase:
    validate_assignee(db, user.organization_id, payload.assigned_user_id)
    case = InvestigationCase(
        organization_id=user.organization_id,
        title=payload.title.strip(),
        description=payload.description,
        created_by_user_id=user.id,
        assigned_user_id=payload.assigned_user_id,
    )
    if not case.title:
        raise HTTPException(status_code=422, detail="Case title cannot be blank")
    db.add(case)
    db.flush()
    db.add(CaseEvent(organization_id=user.organization_id, case_id=case.id, user_id=user.id, event_type="created", details="Case created"))
    db.add(AuditLog(organization_id=user.organization_id, user_id=user.id, event_type="case.created", details=f"case_id={case.id}"))
    db.commit()
    db.refresh(case)
    return case


@router.get("/{case_id}", response_model=CaseResponse)
def read_case(case_id: int, user: CurrentUser, db: DbSession) -> InvestigationCase:
    return get_case(db, case_id, user.organization_id)


@router.patch("/{case_id}", response_model=CaseResponse)
def update_case(case_id: int, payload: CaseUpdate, user: CurrentUser, db: DbSession) -> InvestigationCase:
    case = get_case(db, case_id, user.organization_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one case field must be provided")
    if any(changes.get(key) is None for key in ("title", "status") if key in changes):
        raise HTTPException(status_code=422, detail="Case title and status cannot be null")
    if changes.get("status") == "closed" and user.role not in {"supervisor", "administrator"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Supervisor approval is required to close a case")
    if "assigned_user_id" in changes:
        validate_assignee(db, user.organization_id, changes["assigned_user_id"])
    if "title" in changes and changes["title"] is not None:
        changes["title"] = changes["title"].strip()
        if not changes["title"]:
            raise HTTPException(status_code=422, detail="Case title cannot be blank")
    before = {key: getattr(case, key) for key in changes}
    for key, value in changes.items():
        setattr(case, key, value)
    db.add(
        CaseEvent(
            organization_id=user.organization_id,
            case_id=case.id,
            user_id=user.id,
            event_type="updated",
            details=json.dumps({"before": before, "after": changes}, sort_keys=True),
        )
    )
    db.add(AuditLog(organization_id=user.organization_id, user_id=user.id, event_type="case.updated", details=f"case_id={case.id}"))
    db.commit()
    db.refresh(case)
    return case


@router.post("/{case_id}/transactions", status_code=201)
def link_transaction(
    case_id: int,
    payload: CaseTransactionRequest,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, int]:
    case = get_case(db, case_id, user.organization_id)
    transaction = db.get(BlockchainTransaction, payload.transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    linked = db.scalar(
        select(CaseTransaction.id).where(
            CaseTransaction.case_id == case.id,
            CaseTransaction.transaction_id == transaction.id,
        )
    )
    if linked is not None:
        return {"case_id": case.id, "transaction_id": transaction.id}
    db.add(
        CaseTransaction(
            organization_id=user.organization_id,
            case_id=case.id,
            transaction_id=transaction.id,
            linked_by_user_id=user.id,
        )
    )
    db.add(CaseEvent(organization_id=user.organization_id, case_id=case.id, user_id=user.id, event_type="transaction_linked", details=f"{transaction.network}:{transaction.txid}"))
    db.add(AuditLog(organization_id=user.organization_id, user_id=user.id, event_type="case.transaction_linked", details=f"case_id={case.id}; transaction_id={transaction.id}"))
    db.commit()
    return {"case_id": case.id, "transaction_id": transaction.id}


@router.get("/{case_id}/graph", response_model=CaseGraphResponse)
def read_case_graph(case_id: int, user: CurrentUser, db: DbSession) -> CaseGraphResponse:
    case = get_case(db, case_id, user.organization_id)
    transactions = list(
        db.scalars(
            select(BlockchainTransaction)
            .join(CaseTransaction, CaseTransaction.transaction_id == BlockchainTransaction.id)
            .where(
                CaseTransaction.case_id == case.id,
                CaseTransaction.organization_id == user.organization_id,
            )
            .order_by(BlockchainTransaction.id)
            .limit(MAX_GRAPH_TRANSACTIONS + 1)
        )
    )
    truncated = len(transactions) > MAX_GRAPH_TRANSACTIONS
    transactions = transactions[:MAX_GRAPH_TRANSACTIONS]
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_address_node(address: object, direction: str) -> str | None:
        nonlocal truncated
        if (
            not isinstance(address, str)
            or not address
            or len(address) > 128
            or any(ord(character) < 33 or ord(character) > 126 for character in address)
        ):
            return None
        node_id = f"address:{address}"
        existing = nodes.get(node_id)
        if existing is not None:
            if existing["direction"] != direction:
                existing["direction"] = "both"
            return node_id
        if len(nodes) >= MAX_GRAPH_NODES:
            truncated = True
            return None
        nodes[node_id] = {
            "id": node_id,
            "kind": "address",
            "label": address,
            "address": address,
            "direction": direction,
        }
        return node_id

    def add_edge(edge: dict) -> None:
        nonlocal truncated
        if len(edges) >= MAX_GRAPH_EDGES:
            truncated = True
            return
        edges.append(edge)

    for transaction in transactions:
        transaction_node_id = f"transaction:{transaction.txid}"
        observation = transaction.raw_observation
        status_data = observation.get("status", {}) if isinstance(observation, dict) else {}
        nodes[transaction_node_id] = {
            "id": transaction_node_id,
            "kind": "transaction",
            "label": transaction.txid,
            "txid": transaction.txid,
            "network": transaction.network,
            "source": transaction.source,
            "observed_at": transaction.observed_at,
            "is_confirmed": status_data.get("confirmed") if isinstance(status_data, dict) and isinstance(status_data.get("confirmed"), bool) else None,
        }

    for transaction in transactions:
        transaction_node_id = f"transaction:{transaction.txid}"
        observation = transaction.raw_observation
        if len(edges) >= MAX_GRAPH_EDGES:
            truncated = True
            break
        inputs = observation.get("vin", []) if isinstance(observation, dict) else []
        if isinstance(inputs, list):
            for index, item in enumerate(inputs):
                if len(edges) >= MAX_GRAPH_EDGES:
                    truncated = True
                    break
                if not isinstance(item, dict):
                    continue
                prevout = item.get("prevout")
                if not isinstance(prevout, dict):
                    continue
                source_id = add_address_node(prevout.get("scriptpubkey_address"), "input")
                if source_id is None:
                    continue
                value = prevout.get("value")
                add_edge(
                    {
                        "id": f"input:{transaction.txid}:{index}",
                        "source": source_id,
                        "target": transaction_node_id,
                        "direction": "input",
                        "value_satoshis": value if type(value) is int and value >= 0 else None,
                    }
                )

        outputs = observation.get("vout", []) if isinstance(observation, dict) else []
        if isinstance(outputs, list):
            for index, item in enumerate(outputs):
                if len(edges) >= MAX_GRAPH_EDGES:
                    truncated = True
                    break
                if not isinstance(item, dict):
                    continue
                target_id = add_address_node(item.get("scriptpubkey_address"), "output")
                if target_id is None:
                    continue
                value = item.get("value")
                add_edge(
                    {
                        "id": f"output:{transaction.txid}:{index}",
                        "source": transaction_node_id,
                        "target": target_id,
                        "direction": "output",
                        "value_satoshis": value if type(value) is int and value >= 0 else None,
                    }
                )

    db.add(
        AuditLog(
            organization_id=user.organization_id,
            user_id=user.id,
            event_type="case.graph_viewed",
            details=json.dumps(
                {
                    "case_id": case.id,
                    "transactions": len(transactions),
                    "nodes": len(nodes),
                    "edges": len(edges),
                    "truncated": truncated,
                },
                sort_keys=True,
            ),
        )
    )
    db.commit()
    return CaseGraphResponse(
        case_id=case.id,
        nodes=list(nodes.values()),
        edges=edges,
        transaction_count=len(transactions),
        truncated=truncated,
        limitations=[
            "Graph contains only transactions already linked to this case; it does not fetch additional hops.",
            "Addresses are script-level identifiers and do not establish common ownership or real-world attribution.",
            "Missing provider input/output address data is omitted rather than inferred.",
        ],
    )


@router.post("/{case_id}/notes", status_code=201)
def create_note(case_id: int, payload: NoteCreate, user: CurrentUser, db: DbSession) -> dict[str, int]:
    case = get_case(db, case_id, user.organization_id)
    note = AnalystNote(
        organization_id=user.organization_id,
        case_id=case.id,
        user_id=user.id,
        content=payload.content,
    )
    db.add(note)
    db.flush()
    db.add(CaseEvent(organization_id=user.organization_id, case_id=case.id, user_id=user.id, event_type="note_added", details=f"note_id={note.id}"))
    db.add(AuditLog(organization_id=user.organization_id, user_id=user.id, event_type="case.note_added", details=f"case_id={case.id}; note_id={note.id}"))
    db.commit()
    return {"id": note.id}


def build_case_report(case_id: int, user: User, db: Session) -> dict:
    case = get_case(db, case_id, user.organization_id)
    transactions = list(
        db.scalars(
            select(BlockchainTransaction)
            .join(CaseTransaction, CaseTransaction.transaction_id == BlockchainTransaction.id)
            .where(CaseTransaction.case_id == case.id, CaseTransaction.organization_id == user.organization_id)
            .order_by(BlockchainTransaction.id)
        )
    )
    notes = list(
        db.scalars(
            select(AnalystNote)
            .where(AnalystNote.case_id == case.id, AnalystNote.organization_id == user.organization_id)
            .order_by(AnalystNote.created_at)
        )
    )
    evidence = list(
        db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.case_id == case.id, EvidenceItem.organization_id == user.organization_id)
            .order_by(EvidenceItem.created_at)
        )
    )
    events = list(
        db.scalars(
            select(CaseEvent)
            .where(CaseEvent.case_id == case.id, CaseEvent.organization_id == user.organization_id)
            .order_by(CaseEvent.created_at)
        )
    )
    custody_events = (
        list(
            db.scalars(
                select(EvidenceCustodyEvent)
                .where(
                    EvidenceCustodyEvent.organization_id == user.organization_id,
                    EvidenceCustodyEvent.evidence_id.in_([item.id for item in evidence]),
                )
                .order_by(EvidenceCustodyEvent.created_at)
            )
        )
        if evidence
        else []
    )
    return {
        "case": {
            "id": case.id,
            "title": case.title,
            "description": case.description,
            "status": case.status,
            "created_at": case.created_at,
        },
        "methodology": "Public provider observations and analyst-entered notes linked to this case.",
        "findings": "Risk indicators, where present, are heuristic triage signals and are not proof of fraud or attribution.",
        "limitations": ["Provider coverage and confirmation depth may be incomplete.", "The current rules model is uncalibrated."],
        "transactions": [
            {"network": tx.network, "txid": tx.txid, "source": tx.source, "observed_at": tx.observed_at, "raw_observation": tx.raw_observation}
            for tx in transactions
        ],
        "notes": [{"user_id": note.user_id, "content": note.content, "created_at": note.created_at} for note in notes],
        "evidence": [
            {"filename": item.filename, "sha256": item.sha256, "byte_size": item.byte_size, "source_provenance": item.source_provenance}
            for item in evidence
        ],
        "activity": [
            {"user_id": event.user_id, "event_type": event.event_type, "details": event.details, "created_at": event.created_at}
            for event in events
        ],
        "chain_of_custody": [
            {"evidence_id": event.evidence_id, "user_id": event.user_id, "action": event.action, "details": event.details, "created_at": event.created_at}
            for event in custody_events
        ],
    }


def audit_case_report_export(case_id: int, user: User, db: Session, file_format: str) -> None:
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            user_id=user.id,
            event_type="case.report_exported",
            details=f"case_id={case_id}; format={file_format}",
        )
    )
    db.commit()


def spreadsheet_safe_cell(value: object) -> object:
    if isinstance(value, str) and CSV_FORMULA_PREFIX.match(value):
        return f"'{value}"
    return value


@router.get("/{case_id}/report")
def export_case_report(case_id: int, user: CurrentUser, db: DbSession) -> dict:
    report = build_case_report(case_id, user, db)
    audit_case_report_export(case_id, user, db, "json")
    return report


@router.get("/{case_id}/report.csv")
def export_case_report_csv(case_id: int, user: CurrentUser, db: DbSession) -> Response:
    report = build_case_report(case_id, user, db)
    case = report["case"]
    common = {
        "case_id": case["id"],
        "case_title": case["title"],
        "case_description": case["description"],
        "case_status": case["status"],
        "case_created_at": case["created_at"].isoformat(),
        "methodology": report["methodology"],
        "findings": report["findings"],
        "limitations": json.dumps(report["limitations"], ensure_ascii=False),
    }
    rows = [{"record_type": "case", **common}]
    rows.extend(
        {
            "record_type": "transaction",
            **common,
            "network": transaction["network"],
            "txid": transaction["txid"],
            "source": transaction["source"],
            "observed_at": transaction["observed_at"].isoformat(),
            "raw_observation": json.dumps(transaction["raw_observation"], sort_keys=True, ensure_ascii=False),
        }
        for transaction in report["transactions"]
    )
    rows.extend(
        {
            "record_type": "note",
            **common,
            "user_id": note["user_id"],
            "content": note["content"],
            "observed_at": note["created_at"].isoformat(),
        }
        for note in report["notes"]
    )
    rows.extend(
        {
            "record_type": "evidence",
            **common,
            "filename": item["filename"],
            "sha256": item["sha256"],
            "byte_size": item["byte_size"],
            "source_provenance": item["source_provenance"],
        }
        for item in report["evidence"]
    )
    rows.extend(
        {
            "record_type": "activity",
            **common,
            "user_id": event["user_id"],
            "event_type": event["event_type"],
            "details": event["details"],
            "observed_at": event["created_at"].isoformat(),
        }
        for event in report["activity"]
    )
    rows.extend(
        {
            "record_type": "chain_of_custody",
            **common,
            "evidence_id": event["evidence_id"],
            "user_id": event["user_id"],
            "action": event["action"],
            "details": event["details"],
            "observed_at": event["created_at"].isoformat(),
        }
        for event in report["chain_of_custody"]
    )

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CASE_REPORT_CSV_COLUMNS, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(
        {key: spreadsheet_safe_cell(value) for key, value in row.items()}
        for row in rows
    )
    audit_case_report_export(case_id, user, db, "csv")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="case-{case_id}-report.csv"'},
    )


@router.post("/{case_id}/evidence", status_code=201)
async def upload_evidence(
    case_id: int,
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    source_provenance: Annotated[str, Form(min_length=1, max_length=4000)],
) -> dict[str, int | str]:
    case = get_case(db, case_id, user.organization_id)
    content = await file.read(MAX_EVIDENCE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="Evidence file cannot be empty")
    if len(content) > MAX_EVIDENCE_BYTES:
        raise HTTPException(status_code=413, detail="Evidence files are limited to 10 MiB")
    if not source_provenance.strip():
        raise HTTPException(status_code=422, detail="Evidence source provenance cannot be blank")
    clean_filename = SAFE_FILENAME.sub("", PurePosixPath((file.filename or "evidence.bin").replace("\\", "/")).name)[:255]
    if not clean_filename:
        clean_filename = "evidence.bin"

    storage_key = f"{uuid.uuid4().hex}.evidence"
    storage_root = settings.evidence_storage_path.resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    path = (storage_root / storage_key).resolve()
    if path.parent != storage_root:
        raise HTTPException(status_code=500, detail="Evidence storage path is invalid")
    with path.open("xb") as evidence_file:
        evidence_file.write(content)

    item = EvidenceItem(
        organization_id=user.organization_id,
        case_id=case.id,
        uploaded_by_user_id=user.id,
        filename=clean_filename,
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="application/octet-stream",
        storage_key=storage_key,
        byte_size=len(content),
        source_provenance=source_provenance.strip(),
    )
    try:
        db.add(item)
        db.flush()
        db.add(EvidenceCustodyEvent(organization_id=user.organization_id, evidence_id=item.id, user_id=user.id, action="uploaded"))
        db.add(CaseEvent(organization_id=user.organization_id, case_id=case.id, user_id=user.id, event_type="evidence_uploaded", details=f"evidence_id={item.id}; sha256={item.sha256}"))
        db.add(AuditLog(organization_id=user.organization_id, user_id=user.id, event_type="case.evidence_uploaded", details=f"case_id={case.id}; evidence_id={item.id}"))
        db.commit()
    except SQLAlchemyError:
        path.unlink(missing_ok=True)
        raise
    return {"id": item.id, "filename": item.filename, "sha256": item.sha256, "byte_size": item.byte_size}


@router.get("/{case_id}/evidence")
def list_evidence(case_id: int, user: CurrentUser, db: DbSession) -> list[dict]:
    case = get_case(db, case_id, user.organization_id)
    items = db.scalars(
        select(EvidenceItem)
        .where(EvidenceItem.case_id == case.id, EvidenceItem.organization_id == user.organization_id)
        .order_by(EvidenceItem.created_at.desc())
    )
    return [
        {
            "id": item.id,
            "filename": item.filename,
            "sha256": item.sha256,
            "content_type": item.content_type,
            "byte_size": item.byte_size,
            "source_provenance": item.source_provenance,
            "created_at": item.created_at,
        }
        for item in items
    ]


@router.get("/{case_id}/evidence/{evidence_id}/download")
def download_evidence(case_id: int, evidence_id: int, user: CurrentUser, db: DbSession) -> FileResponse:
    case = get_case(db, case_id, user.organization_id)
    item = db.scalar(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.case_id == case.id,
            EvidenceItem.organization_id == user.organization_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Evidence item not found")
    storage_root = settings.evidence_storage_path.resolve()
    path = (storage_root / item.storage_key).resolve()
    if path.parent != storage_root or not path.is_file():
        raise HTTPException(status_code=404, detail="Evidence file is unavailable")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item.sha256:
        raise HTTPException(status_code=409, detail="Evidence integrity check failed")

    db.add(EvidenceCustodyEvent(organization_id=user.organization_id, evidence_id=item.id, user_id=user.id, action="downloaded"))
    db.add(AuditLog(organization_id=user.organization_id, user_id=user.id, event_type="case.evidence_downloaded", details=f"case_id={case.id}; evidence_id={item.id}"))
    db.commit()
    return FileResponse(path, filename=item.filename, media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff"})


@router.get("/{case_id}/events")
def list_case_events(
    case_id: int,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[dict]:
    case = get_case(db, case_id, user.organization_id)
    events = db.scalars(
        select(CaseEvent)
        .where(CaseEvent.case_id == case.id, CaseEvent.organization_id == user.organization_id)
        .order_by(CaseEvent.created_at.desc(), CaseEvent.id.desc())
        .limit(limit)
    )
    return [{"id": event.id, "user_id": event.user_id, "event_type": event.event_type, "details": event.details, "created_at": event.created_at} for event in events]
