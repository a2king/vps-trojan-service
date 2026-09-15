from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_admin
from app.models import Admin, TunnelProxy, Vps
from app.schemas import TunnelProxyCreate, TunnelProxyOut, TunnelProxyUpdate
from app.services.nodes import list_vps
from app.services.tunnels import (
    create_tunnel,
    delete_tunnel,
    get_tunnel,
    list_tunnels,
    to_tunnel_out,
    update_tunnel,
)

router = APIRouter(prefix="/api/tunnels", tags=["tunnels"], dependencies=[Depends(get_current_admin)])


def _out(row: TunnelProxy, hops: dict[str, Vps], all_vps: list[Vps]) -> TunnelProxyOut:
    return TunnelProxyOut(**to_tunnel_out(row, hops.get(row.vps_id), all_vps))


@router.get("", response_model=list[TunnelProxyOut])
@router.get("/", response_model=list[TunnelProxyOut], include_in_schema=False)
def read_tunnels(_: Admin = Depends(get_current_admin), db: Session = Depends(get_db)) -> list[TunnelProxyOut]:
    nodes = list_vps(db)
    hops = {item.id: item for item in nodes}
    return [_out(row, hops, nodes) for row in list_tunnels(db)]


@router.post("", response_model=TunnelProxyOut, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=TunnelProxyOut, status_code=status.HTTP_201_CREATED, include_in_schema=False)
def add_tunnel(body: TunnelProxyCreate, db: Session = Depends(get_db)) -> TunnelProxyOut:
    try:
        row = create_tunnel(
            db,
            name=body.name,
            protocol=body.protocol,
            server=body.server,
            port=body.port,
            username=body.username,
            password=body.password,
            vps_id=body.vps_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    nodes = list_vps(db)
    hops = {item.id: item for item in nodes}
    return _out(row, hops, nodes)


@router.patch("/{tunnel_id}", response_model=TunnelProxyOut)
def patch_tunnel(
    tunnel_id: int,
    body: TunnelProxyUpdate,
    db: Session = Depends(get_db),
) -> TunnelProxyOut:
    row = get_tunnel(db, tunnel_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="隧道代理不存在")
    payload = body.model_dump(exclude_unset=True)
    try:
        row = update_tunnel(db, row, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    nodes = list_vps(db)
    hops = {item.id: item for item in nodes}
    return _out(row, hops, nodes)


@router.delete("/{tunnel_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_tunnel(tunnel_id: int, db: Session = Depends(get_db)) -> Response:
    row = get_tunnel(db, tunnel_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="隧道代理不存在")
    delete_tunnel(db, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
