from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
import os
import random
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
# CONFIGURATION - 3 PROJECTS (15 BOTS TEST)
# ============================================
PROJECTS = []
for i in range(1, 4):  # Sirf 3 projects (15 bots test)
    PROJECTS.append({
        "id": i,
        "name": f"zoom-bot-{i}",
        "url": f"https://zoom-bot-{i}.railway.app",
        "replicas": 42,
        "bots_per_replica": 5,
        "total_bots": 5,  # Har project mein sirf 5 bots (test ke liye)
        "status": "stopped"
    })

TOTAL_CAPACITY = sum(p["total_bots"] for p in PROJECTS)  # 15 bots

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
    projects: Optional[List[int]] = None
    bot_count: Optional[int] = None  # Total bots across all projects

class ToggleBillingRequest(BaseModel):
    enabled: bool

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
        raise HTTPException(status_code=403, detail="Billing is disabled")
    
    projects_to_use = PROJECTS
    if request.projects:
        projects_to_use = [p for p in PROJECTS if p["id"] in request.projects]
    
    # Calculate bots per project (distribute evenly)
    total_bots_requested = request.bot_count or sum(p["total_bots"] for p in projects_to_use)
    bots_per_project = max(1, total_bots_requested // len(projects_to_use))
    
    # Generate names
    total_bots = len(projects_to_use) * bots_per_project
    names = generate_names(request.name_type, request.custom_names, total_bots)
    
    results = []
    name_index = 0
    
    for project in projects_to_use:
        try:
            project_names = names[name_index:name_index + bots_per_project]
            name_index += bots_per_project
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{project['url']}/api/start-bots",
                    json={
                        "meeting_code": request.meeting_code,
                        "passcode": request.passcode,
                        "bot_count": min(bots_per_project, 5),  # Max 5 per project
                        "duration_minutes": request.duration_minutes,
                        "names": project_names[:5]
                    }
                )
                
                if response.status_code == 200:
                    project["status"] = "running"
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
    
    if request.meeting_code not in active_meetings:
        active_meetings[request.meeting_code] = {
            "meeting_code": request.meeting_code,
            "started_at": datetime.now().isoformat(),
            "total_bots": total_bots,
            "projects": projects_to_use,
            "status": "running"
        }
    
    return {
        "success": True,
        "message": f"Started {total_bots} bots across {len(results)} projects",
        "total_bots": total_bots,
        "results": results
    }

@app.post("/api/kill-meeting")
async def kill_meeting(request: dict):
    """Kill all bots in a meeting"""
    meeting_code = request.get("meeting_code")
    if not meeting_code:
        raise HTTPException(status_code=400, detail="meeting_code required")
    
    if meeting_code not in active_meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    meeting = active_meetings[meeting_code]
    results = []
    
    for project in meeting["projects"]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{project['url']}/api/stop-bots",
                    json={"meeting_code": meeting_code}
                )
                project["status"] = "stopped"
                results.append({"project": project["name"], "status": "stopped"})
        except Exception as e:
            results.append({"project": project["name"], "status": "failed", "error": str(e)})
    
    meeting["status"] = "killed"
    meeting["killed_at"] = datetime.now().isoformat()
    
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
        for meeting_code, meeting in active_meetings.items():
            if meeting["status"] == "running":
                meeting["status"] = "paused"
    
    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused"
    }

@app.get("/api/status")
async def get_status():
    total_bots = 0
    running_bots = 0
    
    for p in PROJECTS:
        total_bots += p["total_bots"]
        if p["status"] == "running":
            running_bots += p["total_bots"]
    
    return {
        "billing_enabled": billing_enabled,
        "active_meetings": active_meetings,
        "total_bots": total_bots,
        "running_bots": running_bots,
        "projects": [
            {
                "name": p["name"], 
                "status": p["status"], 
                "total_bots": p["total_bots"],
                "url": p["url"]
            } 
            for p in PROJECTS
        ]
    }

@app.get("/health")
async def health():
    return {
        "online": True,
        "capacity": TOTAL_CAPACITY,
        "projects": len(PROJECTS)
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
