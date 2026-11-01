from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import CurrentUser, bearer_scheme
from app.models import AuditLog, Organization, User, UserInvitation, UserSession
from app.schemas import InvitationAccept, LoginRequest, TokenResponse, UserResponse
from app.security import digest_token, hash_password, issue_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/accept-invitation", response_model=UserResponse, status_code=201)
def accept_invitation(payload: InvitationAccept, db: DbSession) -> User:
    now = datetime.now(timezone.utc)
    invitation = db.scalar(
        select(UserInvitation)
        .where(
            UserInvitation.token_digest == digest_token(payload.invitation_token),
            UserInvitation.accepted_at.is_(None),
            UserInvitation.revoked_at.is_(None),
            UserInvitation.expires_at > now,
        )
        .with_for_update()
    )
    if invitation is None:
        raise HTTPException(status_code=400, detail="Invitation is invalid or expired")
    organization = db.get(Organization, invitation.organization_id)
    if organization is None or not organization.is_active:
        raise HTTPException(status_code=400, detail="Invitation is invalid or expired")
    user = User(
        organization_id=invitation.organization_id,
        email=invitation.email,
        password_hash=hash_password(payload.password),
        role=invitation.role,
    )
    db.add(user)
    invitation.accepted_at = now
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invitation is invalid or expired") from exc
    db.add(AuditLog(organization_id=invitation.organization_id, event_type="auth.invitation_accepted", details=f"user_id={user.id}"))
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    email = str(payload.email).strip().lower()
    user = db.scalar(
        select(User)
        .join(Organization)
        .where(User.email == email, User.is_active.is_(True), Organization.is_active.is_(True))
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        db.add(AuditLog(event_type="auth.login_failed"))
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = issue_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    db.add(UserSession(user_id=user.id, token_digest=digest_token(token), expires_at=expires_at))
    db.add(AuditLog(organization_id=user.organization_id, user_id=user.id, event_type="auth.login"))
    db.commit()
    return TokenResponse(access_token=token, expires_in=settings.session_ttl_hours * 3600)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    user: CurrentUser,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Response:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired credentials")
    token = db.scalar(
        select(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.token_digest == digest_token(credentials.credentials),
            UserSession.revoked_at.is_(None),
        )
    )
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired credentials")
    token.revoked_at = datetime.now(timezone.utc)
    db.add(AuditLog(organization_id=user.organization_id, user_id=user.id, event_type="auth.logout"))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
def read_current_user(user: CurrentUser) -> User:
    return user
