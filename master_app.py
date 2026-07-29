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
# CONFIGURATION - 4 WORKERS, 42 REPLICAS EACH
# ============================================
PROJECTS = [
    {
        "id": 1,
        "name": "zoom-worker-1",
        "url": "https://zoom-worker-production-9981.up.railway.app",
        "capacity": 5,
        "replicas": 42,
        "total_bots": 210,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 2,
        "name": "zoom-worker-2",
        "url": "https://zoom-worker-production-c2b3.up.railway.app",
        "capacity": 5,
        "replicas": 42,
        "total_bots": 210,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 3,
        "name": "zoom-worker-3",
        "url": "https://zoom-worker-production-fd51.up.railway.app",
        "capacity": 5,
        "replicas": 42,
        "total_bots": 210,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 4,
        "name": "zoom-worker-4",
        "url": "https://zoom-worker-production-ffd8.up.railway.app",
        "capacity": 5,
        "replicas": 42,
        "total_bots": 210,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    }
]

TOTAL_CAPACITY = 840  # 4 * 210

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
active_meetings = {}

# ============================================
# HELPERS
# ============================================
def reset_project(meeting_code):
    for p in PROJECTS:
        if p["active_meeting"] == meeting_code:
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

    # Check existing meeting
    existing_bots = 0
    if request.meeting_code in active_meetings:
        existing_bots = active_meetings[request.meeting_code].get("total_bots", 0)

    # Calculate used capacity
    used_bots = sum(p["used_bots"] for p in PROJECTS)
    available_capacity = TOTAL_CAPACITY - used_bots

    if total_bots_requested > available_capacity:
        raise HTTPException(status_code=400, detail=f"Only {available_capacity} bots available. Requested {total_bots_requested}.")

    # Find idle projects
    available_projects = [p for p in PROJECTS if p["status"] == "idle" and p["used_bots"] == 0]
    if not available_projects:
        raise HTTPException(status_code=400, detail="No idle projects available.")

    # Distribute bots across projects (round-robin)
    allocated = []
    remaining = total_bots_requested
    # We'll allocate per worker capacity (210 each) as needed
    for project in available_projects:
        if remaining <= 0:
            break
        # How many can this project take? Up to its total capacity
        take = min(project["total_bots"], remaining)
        # But we need to split into chunks of 5 for each request to worker
        # We'll store the project and the total bots to send to it
        allocated.append({"project": project, "bots": take})
        remaining -= take

    if remaining > 0:
        raise HTTPException(status_code=400, detail="Not enough capacity even after allocation.")

    # Now, for each allocated project, we need to send multiple requests (chunks of 5)
    results = []
    total_started = 0
    assigned_project_ids = []

    for alloc in allocated:
        project = alloc["project"]
        count = alloc["bots"]
        # Split count into chunks of 5
        chunks = [5] * (count // 5)
        if count % 5 != 0:
            chunks.append(count % 5)

        # Send each chunk to the worker
        for chunk in chunks:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"{project['url']}/api/start-bots",
                        json={
                            "meeting_code": request.meeting_code,
                            "passcode": request.passcode,
                            "bot_count": chunk,
                            "duration_minutes": request.duration_minutes,
                            "name_type": request.name_type,
                            "custom_names": request.custom_names[:chunk] if request.custom_names else None
                        }
                    )
                    if response.status_code == 200:
                        project["status"] = "running"
                        project["active_meeting"] = request.meeting_code
                        project["used_bots"] += chunk
                        if project["id"] not in assigned_project_ids:
                            assigned_project_ids.append(project["id"])
                        total_started += chunk
                        results.append({
                            "project": project["name"],
                            "status": "success",
                            "bots": chunk
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

    # Update meeting entry (cumulative)
    if request.meeting_code in active_meetings:
        meeting = active_meetings[request.meeting_code]
        meeting["total_bots"] = meeting.get("total_bots", 0) + total_started
        meeting["projects"] = meeting.get("projects", []) + assigned_project_ids
        meeting["duration"] = request.duration_minutes
        meeting["status"] = "running"
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

    # Kill bots on each assigned project
    for project_id in meeting.get("projects", []):
        project = next((p for p in PROJECTS if p["id"] == project_id), None)
        if project and project["active_meeting"] == meeting_code:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        f"{project['url']}/api/stop-bots",
                        json={"meeting_code": meeting_code}
                    )
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
        # Kill all active meetings
        meetings_to_kill = list(active_meetings.keys())
        for meeting_code in meetings_to_kill:
            meeting = active_meetings.get(meeting_code)
            if meeting:
                for project_id in meeting.get("projects", []):
                    project = next((p for p in PROJECTS if p["id"] == project_id), None)
                    if project and project["active_meeting"] == meeting_code:
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
                del active_meetings[meeting_code]

    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused (All bots killed instantly)"
    }

@app.get("/api/status")
async def get_status():
    running_bots = sum(p["used_bots"] for p in PROJECTS if p["status"] == "running")
    total_bots = sum(p["total_bots"] for p in PROJECTS)

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
                "replicas": p["replicas"],
                "total_bots": p["total_bots"],
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
