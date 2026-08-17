from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
import asyncio
import os
from typing import List, Optional
from datetime import datetime, timezone, timedelta

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# CONFIGURATION
# ============================================
STATIC_WORKERS = [
    {
        "id": 1,
        "name": "zoom-worker-1",
        "url": "https://zoom-worker-production-9981.up.railway.app",  # ← apna worker URL daal
        "capacity": 35,          # 30 bots ke liye thoda buffer
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    }
]

REGISTERED_WORKERS = []

# ============================================
# MODELS
# ============================================
class StartBotsRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    duration_minutes: int = 60
    name_type: str = "indian"
    custom_names: Optional[List[str]] = None
    bot_count: int = 15

class KillMeetingRequest(BaseModel):
    meeting_code: str

class RegisterWorkerRequest(BaseModel):
    worker_id: str
    url: str
    capacity: int = 20

# ============================================
# STATE
# ============================================
billing_enabled = True
active_meetings = {}

# ============================================
# HELPERS
# ============================================
def get_all_workers():
    all_workers = []
    for w in STATIC_WORKERS:
        all_workers.append(w.copy())
    for i, w in enumerate(REGISTERED_WORKERS):
        all_workers.append({
            "id": 100 + i,
            "name": w["worker_id"],
            "url": w["url"],
            "capacity": w["capacity"],
            "status": w.get("status", "idle"),
            "active_meeting": w.get("active_meeting"),
            "used_bots": w.get("used_bots", 0)
        })
    return all_workers

# ============================================
# API ENDPOINTS
# ============================================
@app.get("/")
async def root():
    workers = get_all_workers()
    total_capacity = sum(w["capacity"] for w in workers)
    running = sum(w.get("used_bots", 0) for w in workers)
    return {
        "message": "Master Controller Running",
        "billing_enabled": billing_enabled,
        "total_capacity": total_capacity,
        "running_bots": running,
        "available_bots": total_capacity - running
    }

@app.post("/api/register-worker")
async def register_worker(request: RegisterWorkerRequest):
    for w in REGISTERED_WORKERS:
        if w["url"] == request.url:
            return {"message": "Worker already registered", "worker_id": w["worker_id"]}
    
    REGISTERED_WORKERS.append({
        "worker_id": request.worker_id,
        "url": request.url,
        "capacity": request.capacity,
        "status": "idle",
        "used_bots": 0,
        "active_meeting": None
    })
    print(f"✅ Registered: {request.worker_id} | {request.url}")
    return {"message": "Worker registered successfully", "worker_id": request.worker_id}

@app.post("/api/start-bots")
async def start_bots(request: StartBotsRequest):
    global billing_enabled, active_meetings

    if not billing_enabled:
        raise HTTPException(status_code=403, detail="Billing is disabled.")

    total_needed = request.bot_count
    if total_needed < 1:
        raise HTTPException(status_code=400, detail="Bot count must be at least 1.")

    all_workers = get_all_workers()
    idle_workers = [w for w in all_workers if w["status"] == "idle" and w.get("used_bots", 0) == 0]

    if not idle_workers:
        raise HTTPException(status_code=400, detail="No idle workers available.")

    total_capacity = sum(w["capacity"] for w in idle_workers)
    if total_needed > total_capacity:
        raise HTTPException(
            status_code=400, 
            detail=f"Requested {total_needed} bots, available capacity is only {total_capacity}."
        )

    results = []
    total_started = 0
    allocated_workers = []

    for worker in idle_workers:
        if total_needed <= 0:
            break

        take = min(worker["capacity"], total_needed)
        
        # Update worker state
        worker["used_bots"] = take
        worker["status"] = "busy"
        worker["active_meeting"] = request.meeting_code

        # Also update original STATIC_WORKERS
        for sw in STATIC_WORKERS:
            if sw["url"] == worker["url"]:
                sw["used_bots"] = take
                sw["status"] = "busy"
                sw["active_meeting"] = request.meeting_code
                break

        total_started += take
        total_needed -= take
        allocated_workers.append(worker)

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(
                    f"{worker['url']}/api/start-bots",
                    json={
                        "meeting_code": request.meeting_code,
                        "passcode": request.passcode,
                        "bot_count": take,
                        "duration_minutes": request.duration_minutes,
                        "name_type": request.name_type,
                        "custom_names": request.custom_names[:take] if request.custom_names else None
                    },
                    follow_redirects=True
                )
                if resp.status_code == 200:
                    results.append({"worker": worker["name"], "status": "success", "bots": take})
                else:
                    results.append({"worker": worker["name"], "status": "failed", "error": resp.text[:200]})
                    # Rollback
                    worker["status"] = "idle"
                    worker["active_meeting"] = None
                    worker["used_bots"] = 0
                    for sw in STATIC_WORKERS:
                        if sw["url"] == worker["url"]:
                            sw["status"] = "idle"
                            sw["active_meeting"] = None
                            sw["used_bots"] = 0
        except Exception as e:
            results.append({"worker": worker["name"], "status": "failed", "error": str(e)})
            worker["status"] = "idle"
            worker["active_meeting"] = None
            worker["used_bots"] = 0
            for sw in STATIC_WORKERS:
                if sw["url"] == worker["url"]:
                    sw["status"] = "idle"
                    sw["active_meeting"] = None
                    sw["used_bots"] = 0

    if total_started > 0:
        active_meetings[request.meeting_code] = {
            "started_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            "total_bots": total_started,
            "workers": allocated_workers,
            "status": "running"
        }

    return {
        "success": True,
        "message": f"Started {total_started} bots across {len(allocated_workers)} workers",
        "total_bots": total_started,
        "results": results
    }

@app.post("/api/kill-meeting")
async def kill_meeting(request: KillMeetingRequest):
    meeting_code = request.meeting_code

    if meeting_code not in active_meetings:
        # Force kill on all workers anyway
        all_workers = get_all_workers()
        results = []
        for worker in all_workers:
            try:
                async with httpx.AsyncClient(timeout=12.0) as client:
                    resp = await client.post(
                        f"{worker['url']}/api/stop-bots",
                        json={"meeting_code": meeting_code},
                        follow_redirects=True
                    )
                    results.append({"worker": worker["name"], "status": "force-stopped"})
            except:
                results.append({"worker": worker["name"], "status": "failed"})
        
        # Reset all
        for sw in STATIC_WORKERS:
            sw["status"] = "idle"
            sw["active_meeting"] = None
            sw["used_bots"] = 0
            
        return {"success": True, "message": f"Force killed {meeting_code}", "results": results}

    meeting = active_meetings[meeting_code]
    results = []

    for worker in meeting["workers"]:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    f"{worker['url']}/api/stop-bots",
                    json={"meeting_code": meeting_code},
                    follow_redirects=True
                )
                if resp.status_code == 200:
                    results.append({"worker": worker["name"], "status": "stopped"})
                else:
                    results.append({"worker": worker["name"], "status": "failed", "error": resp.text[:150]})
        except Exception as e:
            results.append({"worker": worker["name"], "status": "failed", "error": str(e)})

        # Reset state
        worker["status"] = "idle"
        worker["active_meeting"] = None
        worker["used_bots"] = 0
        for sw in STATIC_WORKERS:
            if sw["url"] == worker["url"]:
                sw["status"] = "idle"
                sw["active_meeting"] = None
                sw["used_bots"] = 0

    del active_meetings[meeting_code]

    return {
        "success": True,
        "message": f"Killed meeting {meeting_code}",
        "results": results
    }

@app.post("/api/toggle-billing")
async def toggle_billing(request: dict):
    global billing_enabled
    enabled = request.get("enabled", True)
    billing_enabled = enabled

    if not enabled:
        for meeting_code in list(active_meetings.keys()):
            try:
                await kill_meeting(KillMeetingRequest(meeting_code=meeting_code))
            except:
                pass
        active_meetings.clear()

    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused"
    }

@app.get("/api/status")
async def get_status():
    all_workers = get_all_workers()
    running_bots = sum(w.get("used_bots", 0) for w in all_workers)
    total_capacity = sum(w["capacity"] for w in all_workers)

    return {
        "billing_enabled": billing_enabled,
        "active_meetings": active_meetings,
        "total_capacity": total_capacity,
        "running_bots": running_bots,
        "available_bots": total_capacity - running_bots,
        "used_workers": running_bots,          # frontend compatibility
        "available_workers": total_capacity - running_bots,
        "workers": all_workers,
        "registered_workers": REGISTERED_WORKERS
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
