from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
import asyncio
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
# CONFIGURATION — SINGLE WORKER WITH MULTIPLE REPLICAS
# ============================================
WORKER_URL = "https://zoom-worker-production-9981.up.railway.app"  # Your worker URL
BOTS_PER_REQUEST = 10  # Max bots per request (worker capacity per replica)

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

# ============================================
# STATE
# ============================================
billing_enabled = True
active_meetings = {}

# ============================================
# API ENDPOINTS
# ============================================
@app.get("/")
async def root():
    return {
        "message": "Master Controller Running",
        "billing_enabled": billing_enabled
    }

@app.post("/api/start-bots")
async def start_bots(request: StartBotsRequest):
    global billing_enabled, active_meetings
    if not billing_enabled:
        raise HTTPException(status_code=403, detail="Billing is disabled.")

    total_needed = request.bot_count
    if total_needed < 1:
        raise HTTPException(status_code=400, detail="Bot count must be at least 1.")

    # Calculate how many requests needed
    requests_needed = (total_needed + BOTS_PER_REQUEST - 1) // BOTS_PER_REQUEST
    results = []
    total_started = 0
    assigned_workers = []  # track which requests succeeded

    # Send requests sequentially (or concurrently? sequentially is safer)
    for i in range(requests_needed):
        # Determine chunk size (last chunk may be less)
        chunk = min(BOTS_PER_REQUEST, total_needed - total_started)
        if chunk <= 0:
            break
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{WORKER_URL}/api/start-bots",
                    json={
                        "meeting_code": request.meeting_code,
                        "passcode": request.passcode,
                        "bot_count": chunk,
                        "duration_minutes": request.duration_minutes,
                        "name_type": request.name_type,
                        "custom_names": request.custom_names[total_started:total_started+chunk] if request.custom_names else None
                    },
                    follow_redirects=True
                )
                if resp.status_code == 200:
                    total_started += chunk
                    results.append({"request": i+1, "status": "success", "bots": chunk})
                    assigned_workers.append(i+1)
                else:
                    results.append({"request": i+1, "status": "failed", "error": resp.text})
        except Exception as e:
            results.append({"request": i+1, "status": "failed", "error": str(e)})

        # Small delay to avoid hitting the same replica repeatedly
        await asyncio.sleep(0.1)

    # Track meeting if at least one bot started
    if total_started > 0:
        if request.meeting_code not in active_meetings:
            active_meetings[request.meeting_code] = {
                "started_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
                "total_bots": total_started,
                "requests": assigned_workers,
                "duration": request.duration_minutes,
                "status": "running"
            }
        else:
            meeting = active_meetings[request.meeting_code]
            meeting["total_bots"] += total_started
            meeting["requests"] += assigned_workers
            meeting["status"] = "running"

    return {
        "success": True,
        "message": f"Started {total_started} bots for meeting {request.meeting_code}.",
        "total_bots": total_started,
        "requests_sent": requests_needed,
        "results": results
    }

@app.post("/api/kill-meeting")
async def kill_meeting(request: KillMeetingRequest):
    meeting_code = request.meeting_code
    if meeting_code not in active_meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")

    meeting = active_meetings[meeting_code]
    # Send stop request multiple times to hit all replicas
    stop_requests = meeting.get("requests", [])
    if not stop_requests:
        stop_requests = list(range(1, 43))  # if we don't know, try 42 times

    results = []
    for i in range(len(stop_requests)):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{WORKER_URL}/api/stop-bots",
                    json={"meeting_code": meeting_code},
                    follow_redirects=True
                )
                if resp.status_code == 200:
                    results.append({"attempt": i+1, "status": "stopped"})
                else:
                    results.append({"attempt": i+1, "status": "failed", "error": resp.text})
        except Exception as e:
            results.append({"attempt": i+1, "status": "failed", "error": str(e)})
        await asyncio.sleep(0.1)

    # Mark meeting killed
    meeting["status"] = "killed"
    meeting["killed_at"] = datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat()

    return {
        "success": True,
        "message": f"Killed meeting {meeting_code}.",
        "results": results
    }

@app.post("/api/toggle-billing")
async def toggle_billing(request: dict):
    global billing_enabled
    enabled = request.get("enabled", True)
    billing_enabled = enabled
    if not enabled:
        # Kill all meetings by sending multiple stop requests
        for meeting_code in list(active_meetings.keys()):
            meeting = active_meetings[meeting_code]
            for _ in range(42):
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.post(
                            f"{WORKER_URL}/api/stop-bots",
                            json={"meeting_code": meeting_code},
                            follow_redirects=True
                        )
                except:
                    pass
                await asyncio.sleep(0.05)
            meeting["status"] = "paused"
    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused"
    }

@app.get("/api/status")
async def get_status():
    return {
        "billing_enabled": billing_enabled,
        "active_meetings": active_meetings,
        "total_bots_requested": sum(m["total_bots"] for m in active_meetings.values() if m["status"] == "running")
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
