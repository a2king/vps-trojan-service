from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_admin
from app.models import Admin, Vps
from app.schemas import VpsCreate, VpsLogOut, VpsOut, VpsUpdate
from app.services import install_logs, node_install, ssh
from app.services.nodes import (
    get_vps,
    is_valid_domain,
    list_vps,
    local_vps_id,
    normalize_domain,
    to_vps_out,
)
from app.timeutil import now_local

router = APIRouter(prefix="/api/vps", tags=["vps"], dependencies=[Depends(get_current_admin)])


def _out(row: Vps) -> VpsOut:
    return VpsOut(**to_vps_out(row))


@router.get("", response_model=list[VpsOut])
@router.get("/", response_model=list[VpsOut], include_in_schema=False)
def read_nodes(_: Admin = Depends(get_current_admin), db: Session = Depends(get_db)) -> list[VpsOut]:
    return [_out(row) for row in list_vps(db)]


@router.post("", response_model=VpsOut, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=VpsOut, status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_node(body: VpsCreate, db: Session = Depends(get_db)) -> VpsOut:
    domain = normalize_domain(body.id)
    if not is_valid_domain(domain):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="域名无效")
    if db.get(Vps, domain) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该节点已存在")
    if domain == local_vps_id():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="这是本机节点，无需再添加")
    ssh.ensure_host_key()
    name = (body.name or "").strip() or domain.split(".")[0]
    row = Vps(
        id=domain,
        name=name,
        ssh_host=body.ssh_host.strip(),
        ssh_port=int(body.ssh_port or 22),
        ssh_user=(body.ssh_user or "root").strip() or "root",
        is_local=0,
        hidden=1 if body.hidden else 0,
        status="installing",
        last_error="",
        created_at=now_local(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        node_install.start_install_job(row.id, body.ssh_password)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(row)


@router.patch("/{vps_id}", response_model=VpsOut)
def update_node(vps_id: str, body: VpsUpdate, db: Session = Depends(get_db)) -> VpsOut:
    row = get_vps(db, vps_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="节点不存在")
    if body.name is not None:
        row.name = body.name.strip() or row.id
    if body.hidden is not None:
        row.hidden = 1 if body.hidden else 0
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete("/{vps_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node(vps_id: str, db: Session = Depends(get_db)) -> Response:
    row = get_vps(db, vps_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="节点不存在")
    if row.is_local:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="本机节点不能删除")
    node_install.stop_tunnel(row)
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{vps_id}/logs", response_model=VpsLogOut)
def read_logs(vps_id: str, db: Session = Depends(get_db)) -> VpsLogOut:
    row = get_vps(db, vps_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="节点不存在")
    return VpsLogOut(id=row.id, status=row.status, log=install_logs.read_log(row.id))


@router.post("/{vps_id}/reinstall", response_model=VpsOut)
def reinstall_node(vps_id: str, body: VpsCreate, db: Session = Depends(get_db)) -> VpsOut:
    row = get_vps(db, vps_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="节点不存在")
    if row.is_local:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="本机节点无需远程安装")
    row.ssh_host = body.ssh_host.strip() or row.ssh_host
    row.ssh_port = int(body.ssh_port or row.ssh_port or 22)
    row.ssh_user = (body.ssh_user or row.ssh_user or "root").strip() or "root"
    if body.name:
        row.name = body.name.strip()
    row.status = "installing"
    row.last_error = ""
    db.commit()
    db.refresh(row)
    try:
        node_install.start_install_job(row.id, body.ssh_password)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(row)
