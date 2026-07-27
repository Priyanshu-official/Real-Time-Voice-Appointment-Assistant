from datetime import date
from typing import Optional, Union
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.services.identity import verify_patient_identity
from app.db.session import get_db

router = APIRouter(tags=["identity"])


class IdentityVerificationRequest(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: Union[date, str]


class IdentityVerificationResponse(BaseModel):
    verified: bool
    patient_id: Optional[int] = None
    message: str


@router.post("/verify-identity", response_model=IdentityVerificationResponse)
def verify_identity(
    payload: IdentityVerificationRequest,
    db: Session = Depends(get_db),
):
    verified, patient_id, message = verify_patient_identity(
        db=db,
        first_name=payload.first_name,
        last_name=payload.last_name,
        date_of_birth=payload.date_of_birth,
    )
    return IdentityVerificationResponse(
        verified=verified,
        patient_id=patient_id,
        message=message,
    )
