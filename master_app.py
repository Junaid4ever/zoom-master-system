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
# CONFIGURATION — Update with your worker URL and capacity=50
# ============================================
PROJECTS = [
    {
        "id": 1,
        "name": "zoom-worker-1",
        "url": "https://your-worker-url.railway.app",  # <-- Replace with your actual worker URL
        "capacity": 50,  # <-- Set to 50
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    }
    # Add more workers if you have multiple, each with capacity 50
]

TOTAL_CAPACITY = sum(p["capacity"] for p in PROJECTS)

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

    # Check available capacity
    used_bots = sum(p["used_bots"] for p in PROJECTS)
    available = TOTAL_CAPACITY - used_bots
    if total_needed > available:
        raise HTTPException(status_code=400, detail=f"Only {available} bots available, requested {total_needed}.")

    # Find idle projects with enough capacity
    allocated = []
    remaining = total_needed
    for project in PROJECTS:
        if project["status"] == "idle" and project["used_bots"] == 0:
            take = min(project["capacity"], remaining)
            if take > 0:
                allocated.append({"project": project, "bots": take})
                remaining -= take
        if remaining == 0:
            break

    if remaining > 0:
        raise HTTPException(status_code=400, detail="Could not allocate all bots. Insufficient idle capacity.")

    results = []
    total_started = 0
    assigned_project_ids = []

    for alloc in allocated:
        project = alloc["project"]
        count = alloc["bots"]
        # Worker expects bot_count up to its capacity (now 50), so just send count directly
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
                    assigned_project_ids.append(project["id"])
                    total_started += count
                    results.append({"project": project["name"], "status": "success", "bots": count})
                else:
                    results.append({"project": project["name"], "status": "failed", "error": resp.text})
        except Exception as e:
            results.append({"project": project["name"], "status": "failed", "error": str(e)})

    if request.meeting_code not in active_meetings:
        active_meetings[request.meeting_code] = {
            "started_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            "total_bots": total_started,
            "projects": assigned_project_ids,
            "duration": request.duration_minutes,
            "status": "running"
        }
    else:
        meeting = active_meetings[request.meeting_code]
        meeting["total_bots"] += total_started
        meeting["projects"] = list(set(meeting.get("projects", []) + assigned_project_ids))
        meeting["status"] = "running"

    return {
        "success": True,
        "message": f"Started {total_started} bots for meeting {request.meeting_code}.",
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

    for project_id in meeting.get("projects", []):
        project = next((p for p in PROJECTS if p["id"] == project_id), None)
        if project:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{project['url']}/api/stop-bots",
                        json={"meeting_code": meeting_code},
                        follow_redirects=True
                    )
                    project["status"] = "idle"
                    project["active_meeting"] = None
                    project["used_bots"] = 0
                    results.append({"project": project["name"], "status": "stopped"})
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
        # Kill all active meetings
        for meeting_code in list(active_meetings.keys()):
            meeting = active_meetings[meeting_code]
            for project_id in meeting.get("projects", []):
                project = next((p for p in PROJECTS if p["id"] == project_id), None)
                if project:
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
