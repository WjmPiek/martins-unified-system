"""Signed Martins System entry point for the Attendance application."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.core import User


router = APIRouter()


class MartinsLaunchRequest(BaseModel):
    token: str


class MartinsLaunchResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    main_app_url: str = ""


@router.post("/martins-launch", response_model=MartinsLaunchResponse)
def martins_launch(payload: MartinsLaunchRequest, db: Session = Depends(get_db)):
    """Exchange a short-lived signed Martins handoff for an Attendance JWT."""
    secret = getattr(settings, "ATTENDANCE_LAUNCH_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attendance launch is not configured.",
        )

    serializer = URLSafeTimedSerializer(secret, salt="martins-attendance-launch-v1")
    try:
        handoff = serializer.loads(payload.token, max_age=120)
    except SignatureExpired as exc:
        raise HTTPException(status_code=401, detail="Attendance launch has expired.") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=401, detail="Attendance launch is invalid.") from exc

    if handoff.get("module") != "attendance" or not handoff.get("email"):
        raise HTTPException(status_code=401, detail="Attendance launch is invalid.")

    user = (
        db.query(User)
        .filter(func.lower(User.email) == str(handoff["email"]).strip().lower())
        .first()
    )
    if not user or not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="Your Martins account is not enabled for Attendance yet.",
        )

    token = create_access_token(str(user.id))
    return MartinsLaunchResponse(
        access_token=token,
        main_app_url=str(handoff.get("return_url") or getattr(settings, "MARTINS_MAIN_APP_URL", "")),
    )
