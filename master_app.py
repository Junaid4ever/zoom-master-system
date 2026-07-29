from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
import os
import random
import asyncio
from typing import List, Optional
from datetime import datetime

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
        "bots_per_project": 5,
        "total_bots": 5,
        "status": "stopped",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 2,
        "name": "zoom-worker-2",
        "url": "https://zoom-worker-production-c2b3.up.railway.app",
        "bots_per_project": 5,
        "total_bots": 5,
        "status": "stopped",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 3,
        "name": "zoom-worker-3",
        "url": "https://zoom-worker-production-fd51.up.railway.app",
        "bots_per_project": 5,
        "total_bots": 5,
        "status": "stopped",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 4,
        "name": "zoom-worker-4",
        "url": "https://zoom-worker-production-ffd8.up.railway.app",
        "bots_per_project": 5,
        "total_bots": 5,
        "status": "stopped",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 5,
        "name": "zoom-worker-5",
        "url": "https://zoom-worker-production-6d8d.up.railway.app",
        "bots_per_project": 5,
        "total_bots": 5,
        "status": "stopped",
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
    duration_minutes: int = 10
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
meeting_workers = {}

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
    global billing_enabled, active_meetings, meeting_workers
    
    if not billing_enabled:
        raise HTTPException(status_code=403, detail="Billing is disabled. Enable billing first.")
    
    # Calculate total bots requested
    total_bots_requested = request.bot_count
    if total_bots_requested > TOTAL_CAPACITY:
        raise HTTPException(status_code=400, detail=f"Requested {total_bots_requested} bots, but capacity is {TOTAL_CAPACITY}")
    
    # Find available projects
    available_projects = [p for p in PROJECTS if p["status"] != "running"]
    if not available_projects:
        raise HTTPException(status_code=400, detail="No available projects. All are busy.")
    
    # Distribute bots evenly
    bots_per_project = max(1, total_bots_requested // len(available_projects))
    remaining = total_bots_requested % len(available_projects)
    
    results = []
    assigned_projects = []
    total_started = 0
    
    for idx, project in enumerate(available_projects):
        count = bots_per_project + (1 if idx < remaining else 0)
        if count <= 0:
            continue
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Get names for this project
                name_count = count
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
                    assigned_projects.append(project["id"])
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
    
    # Track meeting
    if request.meeting_code not in active_meetings:
        active_meetings[request.meeting_code] = {
            "meeting_code": request.meeting_code,
            "started_at": datetime.now().isoformat(),
            "total_bots": total_started,
            "bots": total_started,
            "projects": assigned_projects,
            "duration": request.duration_minutes,
            "status": "running"
        }
        meeting_workers[request.meeting_code] = assigned_projects
    else:
        active_meetings[request.meeting_code]["status"] = "running"
        active_meetings[request.meeting_code]["total_bots"] = total_started
    
    return {
        "success": True,
        "message": f"Started {total_started} bots across {len(results)} projects",
        "total_bots": total_started,
        "results": results
    }

@app.post("/api/stop-bots")
async def stop_bots(request: StopBotsRequest):
    """Kill all bots for a meeting immediately + restore capacity"""
    global active_meetings, meeting_workers
    
    meeting_code = request.meeting_code
    
    if meeting_code not in active_meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    meeting = active_meetings[meeting_code]
    results = []
    
    # Stop bots on assigned projects
    for project_id in meeting.get("projects", []):
        project = next((p for p in PROJECTS if p["id"] == project_id), None)
        if project:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        f"{project['url']}/api/stop-bots",
                        json={"meeting_code": meeting_code}
                    )
                    project["status"] = "stopped"
                    project["active_meeting"] = None
                    project["used_bots"] = 0
                    results.append({
                        "project": project["name"],
                        "status": "stopped"
                    })
            except Exception as e:
                results.append({
                    "project": project["name"],
                    "status": "failed",
                    "error": str(e)
                })
    
    meeting["status"] = "killed"
    meeting["killed_at"] = datetime.now().isoformat()
    
    # Clean up
    if meeting_code in meeting_workers:
        del meeting_workers[meeting_code]
    
    return {
        "success": True,
        "message": f"Killed meeting {meeting_code} and restored capacity",
        "results": results
    }

@app.post("/api/toggle-billing")
async def toggle_billing(request: ToggleBillingRequest):
    global billing_enabled
    
    billing_enabled = request.enabled
    
    if not billing_enabled:
        # Kill all active meetings
        for meeting_code, meeting in list(active_meetings.items()):
            if meeting["status"] == "running":
                for project_id in meeting.get("projects", []):
                    project = next((p for p in PROJECTS if p["id"] == project_id), None)
                    if project:
                        try:
                            async with httpx.AsyncClient(timeout=5) as client:
                                await client.post(
                                    f"{project['url']}/api/stop-bots",
                                    json={"meeting_code": meeting_code}
                                )
                                project["status"] = "stopped"
                                project["active_meeting"] = None
                                project["used_bots"] = 0
                        except:
                            pass
                meeting["status"] = "paused"
    
    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused (All bots stopped)"
    }

@app.get("/api/status")
async def get_status():
    running_bots = 0
    total_bots = 0
    
    for p in PROJECTS:
        total_bots += p["total_bots"]
        if p["status"] == "running":
            running_bots += p["used_bots"]
    
    # Clean up stale meetings
    current_meetings = {}
    for code, meeting in list(active_meetings.items()):
        # Check if any project is still running for this meeting
        still_running = False
        for project_id in meeting.get("projects", []):
            project = next((p for p in PROJECTS if p["id"] == project_id), None)
            if project and project["status"] == "running" and project["active_meeting"] == code:
                still_running = True
                break
        if still_running:
            current_meetings[code] = meeting
        elif meeting.get("status") != "killed":
            meeting["status"] = "completed"
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
