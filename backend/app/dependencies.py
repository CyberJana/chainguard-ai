from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import User, UserSession
from app.security import digest_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    session = db.scalar(
        select(UserSession)
        .options(joinedload(UserSession.user).joinedload(User.organization))
        .where(UserSession.token_digest == digest_token(credentials.credentials))
    )
    if session is None or session.revoked_at is not None:
        raise unauthorized
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc) or not session.user.is_active or not session.user.organization.is_active:
        raise unauthorized
    return session.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*allowed_roles: str):
    def role_dependency(user: CurrentUser) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return role_dependency
