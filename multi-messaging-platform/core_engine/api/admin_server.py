import docker
from fastapi import APIRouter, Depends, HTTPException
from core_engine.services.rbac import requires_role
from core_engine.models import RoleType
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/server", tags=["admin-server"])

PROJECT_NAME = "multi-messaging-platform"

def get_docker_client():
    try:
        return docker.from_env()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"اتصال به Docker ناموفق: {str(e)}")


@router.post("/start")
async def start_services(
    current_user=Depends(requires_role(RoleType.ADMIN))
):
    try:
        client = get_docker_client()
        containers = client.containers.list(all=True, filters={"name": PROJECT_NAME})
        started = []
        for c in containers:
            if c.status != "running":
                c.start()
                started.append(c.name)
        return {"message": "سرویس‌ها با موفقیت راه‌اندازی شدند", "started": started}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_services(
    current_user=Depends(requires_role(RoleType.ADMIN))
):
    try:
        client = get_docker_client()
        containers = client.containers.list(filters={"name": PROJECT_NAME})
        stopped = []
        for c in containers:
            if c.name != "mmp_core_api":
                c.stop()
                stopped.append(c.name)
        return {"message": "سرویس‌ها با موفقیت متوقف شدند", "stopped": stopped}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restart")
async def restart_services(
    current_user=Depends(requires_role(RoleType.ADMIN))
):
    try:
        client = get_docker_client()
        containers = client.containers.list(filters={"name": PROJECT_NAME})
        restarted = []
        for c in containers:
            if c.name != "mmp_core_api":
                c.restart()
                restarted.append(c.name)
        return {"message": "سرویس‌ها با موفقیت ریستارت شدند", "restarted": restarted}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status(
    current_user=Depends(requires_role(RoleType.ADMIN))
):
    try:
        client = get_docker_client()
        containers = client.containers.list(
            all=True,
            filters={"name": PROJECT_NAME}
        )
        services = []
        for c in containers:
            services.append({
                "name": c.name,
                "status": "Up" if c.status == "running" else "Down",
                "state": c.status,
                "uptime": c.attrs.get("State", {}).get("StartedAt", "")[:19]
            })
        return {"services": services, "message": "وضعیت سرویس‌ها دریافت شد"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
