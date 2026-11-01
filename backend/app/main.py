from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.alerts import router as alerts_router
from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.cases import router as cases_router
from app.routes.transactions import router as transactions_router

app = FastAPI(title="ChainGuard AI API", version="0.1.0")
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(transactions_router)
app.include_router(alerts_router)
app.include_router(cases_router)


@app.get("/api/v1/system/health", tags=["system"])
def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ok"}
