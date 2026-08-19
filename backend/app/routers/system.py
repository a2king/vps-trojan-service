from fastapi import APIRouter, Depends

from app.deps import get_current_admin
from app.models import Admin
from app.schemas import SystemOut
from app.services.system import collect_system

router = APIRouter(prefix="/api", tags=["admin"], dependencies=[Depends(get_current_admin)])


@router.get("/system", response_model=SystemOut)
def system(_: Admin = Depends(get_current_admin)) -> SystemOut:
    return SystemOut(**collect_system())
