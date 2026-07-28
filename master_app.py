from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
        "active_meeting": None
    },
    {
        "id": 2,
        "name": "zoom-worker-2",
        "url": "https://zoom-worker-production-c2b3.up.railway.app",
        "bots_per_project": 5,
        "total_bots": 5,
        "status": "stopped",
        "active_meeting": None
    },
    {
        "id": 3,
        "name": "zoom-worker-3",
        "url": "https://zoom-worker-production-fd51.up.railway.app",
        "bots_per_project": 5,
        "total_bots": 5,
        "status": "stopped",
        "active_meeting": None
    },
    {
        "id": 4,
        "name": "zoom-worker-4",
        "url": "https://zoom-worker-production-ffd8.up.railway.app",
        "bots_per_project": 5,
        "total_bots": 5,
        "status": "stopped",
        "active_meeting": None
    },
    {
        "id": 5,
        "name": "zoom-worker-5",
        "url": "https://zoom-worker-production-6d8d.up.railway.app",
        "bots_per_project": 5,
        "total_bots": 5,
        "status": "stopped",
        "active_meeting": None
    }
]

TOTAL_CAPACITY = sum(p["total_bots"] for p in PROJECTS)  # 25 bots

# ============================================
# INDIAN NAMES
# ============================================
INDIAN_FIRST_NAMES = [
    'Aarav', 'Vivaan', 'Aditya', 'Vihaan', 'Arjun', 'Reyansh', 'Ayaan', 
    'Krishna', 'Ishaan', 'Shaurya', 'Rahul', 'Rohan', 'Priya', 'Ananya',
    'Diya', 'Saanvi', 'Aadhya', 'Kavya', 'Riya', 'Anika', 'Amit', 'Rajesh',
    'Sneha', 'Pooja', 'Neha', 'Vikram', 'Karan', 'Manish', 'Suresh', 'Deepak'
]

INDIAN_LAST_NAMES = [
    'Sharma', 'Verma', 'Patel', 'Kumar', 'Singh', 'Reddy', 'Gupta', 'Joshi',
    'Malhotra', 'Mehta', 'Chopra', 'Khanna', 'Agarwal', 'Jain', 'Saxena',
    'Bansal', 'Srivastava', 'Mishra', 'Pandey', 'Rao', 'Desai', 'Nair'
]

ENGLISH_FIRST_NAMES = [
    'James', 'John', 'Robert', 'Michael', 'William', 'David', 'Richard', 'Joseph',
    'Thomas', 'Charles', 'Christopher', 'Daniel', 'Matthew', 'Anthony', 'Donald'
]

ENGLISH_LAST_NAMES = [
    'Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis',
    'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Wilson', 'Anderson', 'Thomas'
]

def generate_names(name_type, custom_names=None, count=1):
    names = []
    if name_type == "indian":
        for _ in range(count):
            first = random.choice(INDIAN_FIRST_NAMES)
            last = random.choice(INDIAN_LAST_NAMES)
            names.append(f"{first} {last}")
    elif name_type == "english":
        for _ in range(count):
            first = random.choice(ENGLISH_FIRST_NAMES)
            last = random.choice(ENGLISH_LAST_NAMES)
            names.append(f"{first} {last}")
    elif name_type == "custom" and custom_names:
        names = custom_names[:count]
        while len(names) < count:
            first = random.choice(INDIAN_FIRST_NAMES)
            last = random.choice(INDIAN_LAST_NAMES)
            names.append(f"{first} {last}")
    return names[:count]

# ============================================
# MODELS
# ============================================
class StartBotsRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    duration_minutes: int = 10
    name_type: str = "indian"
    custom_names: Optional[List[str]] = None
    bot_count: Optional[int] = None

class ToggleBillingRequest(BaseModel):
    enabled: bool

class KillMeetingRequest(BaseModel):
    meeting_code: str

# ============================================
# STATE
# ============================================
billing_enabled = True
active_meetings = {}
project_assignments = {}  # meeting_code -> list of project ids

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
    global billing_enabled, active_meetings, project_assignments
    
    if not billing_enabled:
        raise HTTPException(status_code=403, detail="Billing is disabled. Enable billing first.")
    
    # Calculate bots per project
    total_bots_requested = request.bot_count or TOTAL_CAPACITY
    available_projects = [p for p in PROJECTS if p["status"] == "stopped" or p["status"] == "idle"]
    
    if not available_projects:
        raise HTTPException(status_code=400, detail="No available projects. All are busy.")
    
    # Distribute bots evenly
    bots_per_project = max(1, total_bots_requested // len(available_projects))
    if bots_per_project > 5:
        bots_per_project = 5
    
    total_bots_to_start = len(available_projects) * bots_per_project
    names = generate_names(request.name_type, request.custom_names, total_bots_to_start)
    
    results = []
    name_index = 0
    assigned_projects = []
    
    for project in available_projects:
        try:
            project_names = names[name_index:name_index + bots_per_project]
            name_index += bots_per_project
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{project['url']}/api/start-bots",
                    json={
                        "meeting_code": request.meeting_code,
                        "passcode": request.passcode,
                        "bot_count": bots_per_project,
                        "duration_minutes": request.duration_minutes
                    }
                )
                
                if response.status_code == 200:
                    project["status"] = "running"
                    project["active_meeting"] = request.meeting_code
                    assigned_projects.append(project["id"])
                    results.append({
                        "project": project["name"],
                        "status": "success",
                        "bots": bots_per_project
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
            "total_bots": total_bots_to_start,
            "projects": assigned_projects,
            "status": "running"
        }
        project_assignments[request.meeting_code] = assigned_projects
    
    return {
        "success": True,
        "message": f"Started {total_bots_to_start} bots across {len(results)} projects",
        "total_bots": total_bots_to_start,
        "results": results
    }

@app.post("/api/kill-meeting")
async def kill_meeting(request: KillMeetingRequest):
    global active_meetings, project_assignments
    
    meeting_code = request.meeting_code
    
    if meeting_code not in active_meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    meeting = active_meetings[meeting_code]
    results = []
    
    # Stop bots on assigned projects
    for project_id in meeting["projects"]:
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
    
    # Clean up assignments
    if meeting_code in project_assignments:
        del project_assignments[meeting_code]
    
    return {
        "success": True,
        "message": f"Killed meeting {meeting_code}",
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
                for project_id in meeting["projects"]:
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
    total_bots = 0
    running_bots = 0
    
    for p in PROJECTS:
        total_bots += p["total_bots"]
        if p["status"] == "running":
            running_bots += p["total_bots"]
    
    # Clean up stale meetings
    current_meetings = {}
    for code, meeting in active_meetings.items():
        # Check if any project is still running for this meeting
        still_running = False
        for project_id in meeting["projects"]:
            project = next((p for p in PROJECTS if p["id"] == project_id), None)
            if project and project["status"] == "running" and project["active_meeting"] == code:
                still_running = True
                break
        if still_running:
            current_meetings[code] = meeting
    
    return {
        "billing_enabled": billing_enabled,
        "active_meetings": current_meetings,
        "total_bots": total_bots,
        "running_bots": running_bots,
        "available_bots": total_bots - running_bots,
        "projects": [
            {
                "id": p["id"],
                "name": p["name"],
                "status": p["status"],
                "total_bots": p["total_bots"],
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
