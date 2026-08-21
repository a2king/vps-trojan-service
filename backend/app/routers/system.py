from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_admin
from app.models import Admin
from app.schemas import SystemOut
from app.services.ips import collect_remote_system
from app.services.nodes import get_vps, list_vps, local_vps_id
from app.services.system import collect_system

router = APIRouter(prefix="/api", tags=["admin"], dependencies=[Depends(get_current_admin)])


@router.get("/system", response_model=SystemOut)
def system(
    vps_id: str | None = None,
    _: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> SystemOut:
    target_id = (vps_id or "").strip()
    if not target_id:
        local = get_vps(db, local_vps_id())
        if local is None:
            local = next((item for item in list_vps(db) if item.is_local), None)
        if local is not None:
            target_id = local.id
    if not target_id:
        return SystemOut(**collect_system())
    row = get_vps(db, target_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="节点不存在")
    if row.is_local:
        return SystemOut(**collect_system())
    try:
        return SystemOut(**collect_remote_system(row))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"读取节点状态失败：{exc}") from exc
