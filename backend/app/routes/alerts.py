import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import CurrentUser
from app.models import Alert, AuditLog, BlockchainTransaction, User
from app.schemas_alerts import AlertResponse, AlertUpdate

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[AlertResponse])
def list_alerts(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[str | None, Query(alias="status", pattern="^(open|acknowledged|investigating|dismissed|closed)$")] = None,
    severity: Annotated[str | None, Query(pattern="^(medium|high|critical)$")] = None,
    network: Annotated[str | None, Query(pattern="^[a-z][a-z0-9_-]{0,19}$")] = None,
    assigned_user_id: Annotated[int | None, Query(ge=1)] = None,
    unassigned: bool = False,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[Alert]:
    if assigned_user_id is not None and unassigned:
        raise HTTPException(status_code=422, detail="Choose either assigned_user_id or unassigned, not both")
    for field_name, value in (("created_after", created_after), ("created_before", created_before)):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(status_code=422, detail=f"{field_name} must include a timezone")
    if created_after is not None and created_before is not None and created_after > created_before:
        raise HTTPException(status_code=422, detail="created_after must not be later than created_before")

    statement = select(Alert).where(Alert.organization_id == user.organization_id)
    if status_filter:
        statement = statement.where(Alert.status == status_filter)
    if severity:
        statement = statement.where(Alert.severity == severity)
    if network:
        statement = statement.where(
            Alert.transaction_id.in_(
                select(BlockchainTransaction.id).where(BlockchainTransaction.network == network)
            )
        )
    if assigned_user_id is not None:
        statement = statement.where(Alert.assigned_user_id == assigned_user_id)
    elif unassigned:
        statement = statement.where(Alert.assigned_user_id.is_(None))
    if created_after is not None:
        statement = statement.where(Alert.created_at >= created_after.astimezone(timezone.utc))
    if created_before is not None:
        statement = statement.where(Alert.created_at <= created_before.astimezone(timezone.utc))
    statement = statement.order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(statement))


@router.patch("/{alert_id}", response_model=AlertResponse)
def update_alert(alert_id: int, payload: AlertUpdate, user: CurrentUser, db: DbSession) -> Alert:
    alert = db.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.organization_id == user.organization_id)
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one alert field must be provided")
    if changes.get("status") in {"dismissed", "closed"} and user.role not in {"supervisor", "administrator"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Supervisor approval is required for this alert action")
    assigned_user_id = changes.get("assigned_user_id")
    if assigned_user_id is not None:
        assigned_user = db.scalar(
            select(User).where(
                User.id == assigned_user_id,
                User.organization_id == user.organization_id,
                User.is_active.is_(True),
            )
        )
        if assigned_user is None:
            raise HTTPException(status_code=422, detail="Assigned user must be active and belong to this organization")
    old_values = {key: getattr(alert, key) for key in changes}
    for key, value in changes.items():
        setattr(alert, key, value)
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            user_id=user.id,
            event_type="alert.updated",
            details=json.dumps({"alert_id": alert.id, "before": old_values, "after": changes}, sort_keys=True),
        )
    )
    db.commit()
    db.refresh(alert)
    return alert
