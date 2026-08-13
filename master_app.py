from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
# CONFIGURATION — SINGLE WORKER WITH 50 CAPACITY
# ============================================
PROJECTS = [
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

TOTAL_CAPACITY = 50

# ============================================
# MODELS
# ============================================
class StartBotsRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    duration_minutes: int = 60
    name_type: str = "indian"
    custom_names: Optional[List[str]] = None
    bot_count: int = 50

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
        "total_capacity": TOTAL_CAPACITY,
        "projects": len(PROJECTS),
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
    if total_needed > TOTAL_CAPACITY:
        raise HTTPException(status_code=400, detail=f"Requested {total_needed}, but total capacity is {TOTAL_CAPACITY}.")

    # Check available capacity
    used_bots = sum(p["used_bots"] for p in PROJECTS)
    available = TOTAL_CAPACITY - used_bots
    if total_needed > available:
        raise HTTPException(status_code=400, detail=f"Only {available} bots available, requested {total_needed}.")

    # Only one project, so allocate all to it
    project = PROJECTS[0]
    if project["status"] != "idle" or project["used_bots"] != 0:
        raise HTTPException(status_code=400, detail="Worker is busy with another meeting.")

    # Allocate
    count = total_needed
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{project['url']}/api/start-bots",
                json={
                    "meeting_code": request.meeting_code,
                    "passcode": request.passcode,
                    "bot_count": count,
                    "duration_minutes": request.duration_minutes,
                    "name_type": request.name_type,
                    "custom_names": request.custom_names[:count] if request.custom_names else None
                },
                follow_redirects=True
            )
            if resp.status_code == 200:
                project["status"] = "running"
                project["active_meeting"] = request.meeting_code
                project["used_bots"] = count
                result = {"project": project["name"], "status": "success", "bots": count}
            else:
                result = {"project": project["name"], "status": "failed", "error": resp.text}
                raise HTTPException(status_code=500, detail=resp.text)
    except Exception as e:
        result = {"project": project["name"], "status": "failed", "error": str(e)}
        raise HTTPException(status_code=500, detail=str(e))

    # Track meeting
    if request.meeting_code not in active_meetings:
        active_meetings[request.meeting_code] = {
            "started_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            "total_bots": count,
            "projects": [project["id"]],
            "duration": request.duration_minutes,
            "status": "running"
        }
    else:
        # If meeting already exists, add to it (shouldn't happen with single worker)
        meeting = active_meetings[request.meeting_code]
        meeting["total_bots"] += count
        if project["id"] not in meeting["projects"]:
            meeting["projects"].append(project["id"])
        meeting["status"] = "running"

    return {
        "success": True,
        "message": f"Started {count} bots for meeting {request.meeting_code}.",
        "total_bots": count,
        "results": [result]
    }

@app.post("/api/kill-meeting")
async def kill_meeting(request: KillMeetingRequest):
    meeting_code = request.meeting_code
    if meeting_code not in active_meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")

    meeting = active_meetings[meeting_code]
    results = []
    project = PROJECTS[0]
    if project["active_meeting"] == meeting_code:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{project['url']}/api/stop-bots",
                    json={"meeting_code": meeting_code},
                    follow_redirects=True
                )
                if resp.status_code == 200:
                    project["status"] = "idle"
                    project["active_meeting"] = None
                    project["used_bots"] = 0
                    results.append({"project": project["name"], "status": "stopped"})
                else:
                    results.append({"project": project["name"], "status": "failed", "error": resp.text})
        except Exception as e:
            results.append({"project": project["name"], "status": "failed", "error": str(e)})

    del active_meetings[meeting_code]
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
        for meeting_code in list(active_meetings.keys()):
            meeting = active_meetings[meeting_code]
            project = PROJECTS[0]
            if project["active_meeting"] == meeting_code:
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.post(
                            f"{project['url']}/api/stop-bots",
                            json={"meeting_code": meeting_code},
                            follow_redirects=True
                        )
                        project["status"] = "idle"
                        project["active_meeting"] = None
                        project["used_bots"] = 0
                except:
                    pass
            del active_meetings[meeting_code]

    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused (All bots killed)"
    }

@app.get("/api/status")
async def get_status():
    running_bots = sum(p["used_bots"] for p in PROJECTS if p["status"] == "running")
    return {
        "billing_enabled": billing_enabled,
        "active_meetings": active_meetings,
        "total_capacity": TOTAL_CAPACITY,
        "running_bots": running_bots,
        "available_bots": TOTAL_CAPACITY - running_bots,
        "projects": [
            {
                "id": p["id"],
                "name": p["name"],
                "status": p["status"],
                "capacity": p["capacity"],
                "used_bots": p["used_bots"],
                "url": p["url"],
                "active_meeting": p["active_meeting"]
            }
            for p in PROJECTS
        ]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
