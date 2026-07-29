from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
import os
import random
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
# CONFIGURATION - 5 WORKERS (25 BOTS)
# ============================================
PROJECTS = [
    {
        "id": 1,
        "name": "zoom-worker-1",
        "url": "https://zoom-worker-production-9981.up.railway.app",
        "capacity": 5,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 2,
        "name": "zoom-worker-2",
        "url": "https://zoom-worker-production-c2b3.up.railway.app",
        "capacity": 5,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 3,
        "name": "zoom-worker-3",
        "url": "https://zoom-worker-production-fd51.up.railway.app",
        "capacity": 5,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 4,
        "name": "zoom-worker-4",
        "url": "https://zoom-worker-production-ffd8.up.railway.app",
        "capacity": 5,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 5,
        "name": "zoom-worker-5",
        "url": "https://zoom-worker-production-6d8d.up.railway.app",
        "capacity": 5,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    }
]

TOTAL_CAPACITY = 25

# ============================================
# MODELS
# ============================================
class StartBotsRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    duration_minutes: int = 60
    name_type: str = "indian"
    custom_names: Optional[List[str]] = None
    bot_count: int = 5

class ToggleBillingRequest(BaseModel):
    enabled: bool

class StopBotsRequest(BaseModel):
    meeting_code: str

# ============================================
# STATE
# ============================================
billing_enabled = True
active_meetings = {}  # meeting_code -> meeting data

# ============================================
# HELPER: Reset all projects (for billing off)
# ============================================
def reset_all_projects():
    for p in PROJECTS:
        p["status"] = "idle"
        p["active_meeting"] = None
        p["used_bots"] = 0

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
        raise HTTPException(status_code=403, detail="Billing is disabled. Enable billing first.")
    
    total_bots_requested = request.bot_count
    if total_bots_requested < 1:
        raise HTTPException(status_code=400, detail="Bot count must be at least 1.")
    if total_bots_requested > TOTAL_CAPACITY:
        raise HTTPException(status_code=400, detail=f"Requested {total_bots_requested} bots, but total capacity is {TOTAL_CAPACITY}.")
    
    # Check if meeting already exists and has active bots
    existing_bots = 0
    if request.meeting_code in active_meetings:
        existing_bots = active_meetings[request.meeting_code].get("total_bots", 0)
    
    # Calculate used capacity (projects that are running)
    used_bots = sum(p["used_bots"] for p in PROJECTS)
    available_capacity = TOTAL_CAPACITY - used_bots
    
    # If we are adding more bots to existing meeting, we need to ensure total does not exceed capacity
    new_total = existing_bots + total_bots_requested
    if new_total > TOTAL_CAPACITY:
        raise HTTPException(status_code=400, detail=f"Meeting would have {new_total} bots, exceeding capacity {TOTAL_CAPACITY}.")
    
    # We need to allocate exactly total_bots_requested new bots
    # Find idle projects
    available_projects = [p for p in PROJECTS if p["status"] == "idle" and p["used_bots"] == 0]
    if not available_projects:
        raise HTTPException(status_code=400, detail="No idle projects available.")
    
    # Allocate
    allocated = []
    remaining = total_bots_requested
    for project in available_projects:
        if remaining <= 0:
            break
        take = min(project["capacity"], remaining)
        allocated.append({"project": project, "bots": take})
        remaining -= take
    
    if remaining > 0:
        raise HTTPException(status_code=400, detail="Not enough capacity even after allocation.")
    
    # Start bots on allocated projects
    results = []
    total_started = 0
    assigned_project_ids = []
    
    for alloc in allocated:
        project = alloc["project"]
        count = alloc["bots"]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{project['url']}/api/start-bots",
                    json={
                        "meeting_code": request.meeting_code,
                        "passcode": request.passcode,
                        "bot_count": count,
                        "duration_minutes": request.duration_minutes,
                        "name_type": request.name_type,
                        "custom_names": request.custom_names[:count] if request.custom_names else None
                    }
                )
                if response.status_code == 200:
                    project["status"] = "running"
                    project["active_meeting"] = request.meeting_code
                    project["used_bots"] = count
                    assigned_project_ids.append(project["id"])
                    total_started += count
                    results.append({
                        "project": project["name"],
                        "status": "success",
                        "bots": count
                    })
                else:
                    results.append({
                        "project": project["name"],
                        "status": "failed",
                        "error": response.text
                    })
        except Exception as e:
            results.append({
                "project": project["name"],
                "status": "failed",
                "error": str(e)
            })
    
    # Update or create meeting entry (cumulative)
    if request.meeting_code in active_meetings:
        # Update existing meeting
        meeting = active_meetings[request.meeting_code]
        meeting["total_bots"] = meeting.get("total_bots", 0) + total_started
        meeting["bots"] = meeting["total_bots"]  # for display
        meeting["projects"] = meeting.get("projects", []) + assigned_project_ids
        meeting["duration"] = request.duration_minutes  # update duration if needed
        meeting["status"] = "running"
        # Keep start time as original
    else:
        active_meetings[request.meeting_code] = {
            "meeting_code": request.meeting_code,
            "started_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            "total_bots": total_started,
            "bots": total_started,
            "projects": assigned_project_ids,
            "duration": request.duration_minutes,
            "status": "running",
            "name_type": request.name_type
        }
    
    return {
        "success": True,
        "message": f"Started {total_started} new bots. Meeting now has {active_meetings[request.meeting_code]['total_bots']} bots.",
        "total_bots": active_meetings[request.meeting_code]['total_bots'],
        "results": results
    }

@app.post("/api/stop-bots")
async def stop_bots(request: StopBotsRequest):
    global active_meetings
    
    meeting_code = request.meeting_code
    if meeting_code not in active_meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    meeting = active_meetings[meeting_code]
    results = []
    
    # Stop bots on each project assigned to this meeting
    for project_id in meeting.get("projects", []):
        project = next((p for p in PROJECTS if p["id"] == project_id), None)
        if project:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        f"{project['url']}/api/stop-bots",
                        json={"meeting_code": meeting_code}
                    )
                    # Reset project
                    project["status"] = "idle"
                    project["active_meeting"] = None
                    project["used_bots"] = 0
                    results.append({
                        "project": project["name"],
                        "status": "stopped",
                        "bots_killed": response.json().get("bots_killed", 0)
                    })
            except Exception as e:
                results.append({
                    "project": project["name"],
                    "status": "failed",
                    "error": str(e)
                })
    
    # Remove meeting from active
    total_bots = meeting["total_bots"]
    del active_meetings[meeting_code]
    
    return {
        "success": True,
        "message": f"Killed meeting {meeting_code} with {total_bots} bots, capacity restored.",
        "total_bots_killed": total_bots,
        "results": results
    }

@app.post("/api/toggle-billing")
async def toggle_billing(request: ToggleBillingRequest):
    global billing_enabled, active_meetings
    
    billing_enabled = request.enabled
    
    if not billing_enabled:
        # Kill all active meetings instantly
        meetings_to_kill = list(active_meetings.keys())
        for meeting_code in meetings_to_kill:
            meeting = active_meetings.get(meeting_code)
            if meeting:
                # Kill bots on each project
                for project_id in meeting.get("projects", []):
                    project = next((p for p in PROJECTS if p["id"] == project_id), None)
                    if project:
                        try:
                            async with httpx.AsyncClient(timeout=5) as client:
                                await client.post(
                                    f"{project['url']}/api/stop-bots",
                                    json={"meeting_code": meeting_code}
                                )
                                project["status"] = "idle"
                                project["active_meeting"] = None
                                project["used_bots"] = 0
                        except:
                            pass
                # Remove meeting
                del active_meetings[meeting_code]
    
    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused (All bots killed instantly)"
    }

@app.get("/api/status")
async def get_status():
    running_bots = sum(p["used_bots"] for p in PROJECTS if p["status"] == "running")
    total_bots = sum(p["capacity"] for p in PROJECTS)
    
    # Only include running meetings
    current_meetings = {}
    for code, meeting in list(active_meetings.items()):
        if meeting.get("status") == "running":
            current_meetings[code] = meeting
    
    return {
        "billing_enabled": billing_enabled,
        "active_meetings": current_meetings,
        "total_bots": total_bots,
        "running_bots": running_bots,
        "available_bots": TOTAL_CAPACITY - running_bots,
        "capacity": TOTAL_CAPACITY,
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

@app.get("/health")
async def health():
    return {
        "online": True,
        "capacity": TOTAL_CAPACITY,
        "projects": len(PROJECTS),
        "active_meetings": len(active_meetings)
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
