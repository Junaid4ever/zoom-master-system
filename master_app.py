from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
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

# ========== CONFIG ==========
STATIC_WORKERS = [
    {
        "id": 1,
        "name": "zoom-worker-1",
        "url": "https://zoom-worker-production-9981.up.railway.app",  # ← yahan apna worker URL daalo
        "capacity": 35,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    }
]

billing_enabled = True
active_meetings = {}

# ========== MODELS ==========
class StartBotsRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    duration_minutes: int = 60
    name_type: str = "indian"
    custom_names: Optional[List[str]] = None
    bot_count: int = 15

class KillMeetingRequest(BaseModel):
    meeting_code: str

# ========== HELPERS ==========
def get_all_workers():
    return [w.copy() for w in STATIC_WORKERS]

# ========== ROUTES ==========
@app.get("/")
async def root():
    workers = get_all_workers()
    total = sum(w["capacity"] for w in workers)
    running = sum(w.get("used_bots", 0) for w in workers)
    return {
        "message": "Master Controller Running",
        "billing_enabled": billing_enabled,
        "total_capacity": total,
        "running_bots": running,
        "available_bots": total - running
    }

@app.post("/api/start-bots")
async def start_bots(request: StartBotsRequest):
    global active_meetings

    if not billing_enabled:
        raise HTTPException(status_code=403, detail="Billing is disabled")

    if request.bot_count < 1:
        raise HTTPException(status_code=400, detail="Bot count must be at least 1")

    workers = get_all_workers()
    idle = [w for w in workers if w["status"] == "idle"]

    if not idle:
        raise HTTPException(status_code=400, detail="No idle workers available")

    total_capacity = sum(w["capacity"] for w in idle)
    if request.bot_count > total_capacity:
        raise HTTPException(status_code=400, detail=f"Only {total_capacity} capacity available")

    results = []
    total_started = 0
    allocated = []

    remaining = request.bot_count

    for worker in idle:
        if remaining <= 0:
            break

        take = min(worker["capacity"], remaining)

        # Update state
        for sw in STATIC_WORKERS:
            if sw["url"] == worker["url"]:
                sw["used_bots"] = take
                sw["status"] = "busy"
                sw["active_meeting"] = request.meeting_code
                break

        remaining -= take
        total_started += take
        allocated.append(worker)

        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                resp = await client.post(
                    f"{worker['url']}/api/start-bots",
                    json={
                        "meeting_code": request.meeting_code,
                        "passcode": request.passcode,
                        "bot_count": take,
                        "duration_minutes": request.duration_minutes,
                        "name_type": request.name_type,
                        "custom_names": request.custom_names[:take] if request.custom_names else None
                    }
                )
                if resp.status_code == 200:
                    results.append({"worker": worker["name"], "status": "success", "bots": take})
                else:
                    results.append({"worker": worker["name"], "status": "failed", "error": resp.text[:150]})
                    # rollback
                    for sw in STATIC_WORKERS:
                        if sw["url"] == worker["url"]:
                            sw["status"] = "idle"
                            sw["used_bots"] = 0
                            sw["active_meeting"] = None
        except Exception as e:
            results.append({"worker": worker["name"], "status": "failed", "error": str(e)})
            for sw in STATIC_WORKERS:
                if sw["url"] == worker["url"]:
                    sw["status"] = "idle"
                    sw["used_bots"] = 0
                    sw["active_meeting"] = None

    if total_started > 0:
        active_meetings[request.meeting_code] = {
            "started_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            "total_bots": total_started,
            "workers": allocated,
            "status": "running"
        }

    return {
        "success": True,
        "message": f"Started {total_started} bots",
        "total_bots": total_started,
        "results": results
    }

@app.post("/api/kill-meeting")
async def kill_meeting(request: KillMeetingRequest):
    meeting_code = request.meeting_code
    results = []

    # Try to kill on all workers
    for worker in STATIC_WORKERS:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                await client.post(
                    f"{worker['url']}/api/stop-bots",
                    json={"meeting_code": meeting_code}
                )
            results.append({"worker": worker["name"], "status": "stopped"})
        except Exception as e:
            results.append({"worker": worker["name"], "status": "failed", "error": str(e)})

        worker["status"] = "idle"
        worker["used_bots"] = 0
        worker["active_meeting"] = None

    if meeting_code in active_meetings:
        del active_meetings[meeting_code]

    return {
        "success": True,
        "message": f"Killed meeting {meeting_code}",
        "results": results
    }

@app.get("/api/status")
async def get_status():
    workers = get_all_workers()
    running = sum(w.get("used_bots", 0) for w in workers)
    total = sum(w["capacity"] for w in workers)

    return {
        "billing_enabled": billing_enabled,
        "active_meetings": active_meetings,
        "total_capacity": total,
        "running_bots": running,
        "available_bots": total - running,
        "used_workers": running,
        "available_workers": total - running,
        "workers": workers
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
