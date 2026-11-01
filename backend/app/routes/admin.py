from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import Administrator
from app.models import AuditLog, User, UserInvitation
from app.schemas import InvitationCreate, InvitationResponse, UserCreate, UserResponse, UserUpdate
from app.security import digest_token, hash_password, issue_token

router = APIRouter(prefix="/api/v1/admin", tags=["administration"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/invitations", response_model=InvitationResponse, status_code=201)
def create_invitation(
    payload: InvitationCreate,
    administrator: Administrator,
    db: DbSession,
) -> InvitationResponse:
    email = str(payload.email).strip().lower()
    existing_user = db.scalar(select(User.id).where(User.email == email))
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    now = datetime.now(timezone.utc)
    existing_invitation = db.scalar(
        select(UserInvitation.id).where(
            UserInvitation.organization_id == administrator.organization_id,
            UserInvitation.email == email,
            UserInvitation.accepted_at.is_(None),
            UserInvitation.revoked_at.is_(None),
            UserInvitation.expires_at > now,
        )
    )
    if existing_invitation is not None:
        raise HTTPException(status_code=409, detail="An active invitation already exists")
    token = issue_token()
    expires_at = now + timedelta(hours=24)
    db.add(
        UserInvitation(
            organization_id=administrator.organization_id,
            email=email,
            role=payload.role.value,
            token_digest=digest_token(token),
            created_by_user_id=administrator.id,
            expires_at=expires_at,
        )
    )
    db.add(AuditLog(organization_id=administrator.organization_id, user_id=administrator.id, event_type="admin.invitation_created", details=f"email={email}; role={payload.role.value}"))
    db.commit()
    return InvitationResponse(invitation_token=token, expires_at=expires_at)


@router.get("/users", response_model=list[UserResponse])
def list_users(
    administrator: Administrator,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .where(User.organization_id == administrator.organization_id)
            .order_by(User.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate, administrator: Administrator, db: DbSession) -> User:
    email = str(payload.email).strip().lower()
    user = User(
        organization_id=administrator.organization_id,
        email=email,
        password_hash=hash_password(payload.password),
        role=payload.role.value,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A user with this email already exists") from exc
    db.add(
        AuditLog(
            organization_id=administrator.organization_id,
            user_id=administrator.id,
            event_type="admin.user_created",
            details=f"user_id={user.id}; role={user.role}",
        )
    )
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UserUpdate,
    administrator: Administrator,
    db: DbSession,
) -> User:
    user = db.scalar(
        select(User).where(
            User.id == user_id,
            User.organization_id == administrator.organization_id,
        )
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one user field must be provided")
    if user.id == administrator.id and changes.get("is_active") is False:
        raise HTTPException(status_code=422, detail="Administrators cannot deactivate their own account")
    before = {key: getattr(user, key) for key in changes}
    for key, value in changes.items():
        setattr(user, key, value.value if hasattr(value, "value") else value)
    db.add(
        AuditLog(
            organization_id=administrator.organization_id,
            user_id=administrator.id,
            event_type="admin.user_updated",
            details=f"user_id={user.id}; before={before}; after={changes}",
        )
    )
    db.commit()
    db.refresh(user)
    return user
