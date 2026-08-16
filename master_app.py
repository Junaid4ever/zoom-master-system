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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# CONFIGURATION — Static Worker + Registered Workers
# ============================================
STATIC_WORKERS = [
    {
        "id": 1,
        "name": "zoom-worker-1",
        "url": "https://zoom-worker-production-9981.up.railway.app",
        "capacity": 50,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    }
]

# Dynamic registered workers (from CodeSandbox)
REGISTERED_WORKERS = []  # each: {"worker_id": str, "url": str, "capacity": int, "status": str}

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
    """Combine static and registered workers."""
    all_workers = STATIC_WORKERS.copy()
    for i, w in enumerate(REGISTERED_WORKERS):
        all_workers.append({
            "id": 100 + i,
            "name": w["worker_id"],
            "url": w["url"],
            "capacity": w["capacity"],
            "status": "idle",
            "active_meeting": None,
            "used_bots": 0
        })
    return all_workers

# ============================================
# API ENDPOINTS
# ============================================
@app.get("/")
async def root():
    return {
        "message": "Master Controller Running",
        "billing_enabled": billing_enabled,
        "total_capacity": sum(w["capacity"] for w in get_all_workers() if w["status"] != "busy")
    }

@app.post("/api/register-worker")
async def register_worker(request: RegisterWorkerRequest):
    """Register a new worker (from CodeSandbox) - update if already exists"""
    for w in REGISTERED_WORKERS:
        if w["worker_id"] == request.worker_id:
            w["url"] = request.url
            w["capacity"] = request.capacity
            print(f"🔄 Updated worker: {request.worker_id} -> {request.url}")
            return {"message": "Worker updated", "worker_id": request.worker_id}
    
    REGISTERED_WORKERS.append({
        "worker_id": request.worker_id,
        "url": request.url,
        "capacity": request.capacity,
        "status": "idle"
    })
    print(f"✅ Registered new worker: {request.worker_id} ({request.url})")
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
        raise HTTPException(status_code=400, detail=f"Requested {total_needed} bots, but available capacity is {total_capacity}.")

    results = []
    total_started = 0
    allocated_workers = []

    for worker in idle_workers:
        if total_needed <= 0:
            break
        take = min(worker["capacity"], total_needed)
        worker["used_bots"] = take
        worker["status"] = "busy"
        worker["active_meeting"] = request.meeting_code
        total_started += take
        total_needed -= take
        allocated_workers.append(worker)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
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
                    results.append({"worker": worker["name"], "status": "failed", "error": resp.text})
                    worker["status"] = "idle"
                    worker["active_meeting"] = None
                    worker["used_bots"] = 0
        except Exception as e:
            results.append({"worker": worker["name"], "status": "failed", "error": str(e)})
            worker["status"] = "idle"
            worker["active_meeting"] = None
            worker["used_bots"] = 0

    if request.meeting_code not in active_meetings:
        active_meetings[request.meeting_code] = {
            "started_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            "total_bots": total_started,
            "workers": allocated_workers,
            "status": "running"
        }
    else:
        active_meetings[request.meeting_code]["total_bots"] += total_started
        active_meetings[request.meeting_code]["workers"] += allocated_workers

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
        raise HTTPException(status_code=404, detail="Meeting not found")

    meeting = active_meetings[meeting_code]
    results = []

    for worker in meeting["workers"]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{worker['url']}/api/stop-bots",
                    json={"meeting_code": meeting_code},
                    follow_redirects=True
                )
                if resp.status_code == 200:
                    worker["status"] = "idle"
                    worker["active_meeting"] = None
                    worker["used_bots"] = 0
                    results.append({"worker": worker["name"], "status": "stopped"})
                else:
                    results.append({"worker": worker["name"], "status": "failed", "error": resp.text})
        except Exception as e:
            results.append({"worker": worker["name"], "status": "failed", "error": str(e)})

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
            meeting = active_meetings[meeting_code]
            for worker in meeting["workers"]:
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.post(
                            f"{worker['url']}/api/stop-bots",
                            json={"meeting_code": meeting_code},
                            follow_redirects=True
                        )
                        worker["status"] = "idle"
                        worker["active_meeting"] = None
                        worker["used_bots"] = 0
                except:
                    pass
            del active_meetings[meeting_code]
    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused"
    }

@app.get("/api/status")
async def get_status():
    all_workers = get_all_workers()
    running_bots = sum(w.get("used_bots", 0) for w in all_workers if w["status"] == "busy")
    total_capacity = sum(w["capacity"] for w in all_workers)
    return {
        "billing_enabled": billing_enabled,
        "active_meetings": active_meetings,
        "total_capacity": total_capacity,
        "running_bots": running_bots,
        "available_bots": total_capacity - running_bots,
        "workers": all_workers,
        "registered_workers": REGISTERED_WORKERS
    }

# ============================================
# RUN — Dynamic Port for Railway
# ============================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
