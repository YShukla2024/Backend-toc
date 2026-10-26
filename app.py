# -----------------------------
# IMPORTS
# -----------------------------
import os
import json
import re
import shutil
import sys
from pathlib import Path
import pysqlite3

# Fix SQLite version issue on Azure
sys.modules["sqlite3"] = pysqlite3

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from typing import Optional

# Services
from agents.orchestrator import TOCOrchestrator
from services.vector_service import VectorService
from database.db_manager import DBManager
from services.email_service import EmailService
from document.word_generator import WordTOCGenerator

print("APP ENTRY HIT")

app = FastAPI(title="Agentic TOC API")


# -----------------------------
# LAZY VECTOR INIT
# (Avoids crash on cold start if resources not ready)
# -----------------------------
_vector_service = None

def get_vector_service():
    global _vector_service
    if _vector_service is None:
        print("Initializing VectorService...")
        _vector_service = VectorService()
    return _vector_service


# -----------------------------
# INIT SERVICES
# -----------------------------
db = DBManager()
email_service = EmailService()
word_generator = WordTOCGenerator()

# Orchestrator uses lazy vector service
orchestrator = TOCOrchestrator(get_vector_service())


# -----------------------------
# HELPERS
# -----------------------------
def normalize_toc(toc):
    normalized_days = []

    for i, day in enumerate(toc.get("days", [])):
        topics = []

        if isinstance(day, dict):
            if "topics" in day:
                topics = day["topics"]
            elif "content" in day:
                topics = day["content"]
            else:
                values = list(day.values())
                if values:
                    topics = values[0]

        normalized_days.append({
            "day": f"Day {i+1}",
            "topics": topics or []
        })

    toc["days"] = normalized_days
    return toc


def get_display_date(created_at, approved_at):
    return approved_at or created_at or None


# -----------------------------
# CORS
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# MODELS
# -----------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str

class GenerateRequest(BaseModel):
    requirements: str
    user_email: str

class SendToSMERequest(BaseModel):
    version_id: int
    sme_email: EmailStr
    kam_email: EmailStr

class ReviewRequest(BaseModel):
    version_id: int
    feedback: Optional[str] = ""

class RegenerateRequest(BaseModel):
    version_id: int

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    new_password: str


# -----------------------------
# HEALTH
# -----------------------------
@app.get("/")
def root():
    return {"status": "API running"}


# -----------------------------
# AUTH
# -----------------------------
@app.post("/login")
def login(data: LoginRequest):
    try:
        user = db.authenticate_user(data.email, data.password)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        name, role = user

        return {"name": name, "email": data.email, "role": role}

    except HTTPException:
        raise
    except Exception as e:
        print("LOGIN ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/register")
def register(data: RegisterRequest):
    try:
        db.create_user(
            name=data.name,
            email=data.email,
            password=data.password,
            role=data.role
        )
        return {"message": "User registered successfully"}

    except Exception as e:
        print("REGISTER ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset-password")
def reset_password(data: ResetPasswordRequest):
    try:
        user = db.get_user_by_email(data.email)

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        db.update_password(data.email, data.new_password)
        return {"message": "Password updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        print("RESET PASSWORD ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# GENERATE TOC
# -----------------------------
@app.post("/generate")
def generate_toc(request: GenerateRequest):
    try:
        result = orchestrator.run(request.requirements)

        # FIX: orchestrator.run() returns a dict with "toc" and "requirements" keys
        toc = result["toc"]
        requirements = result["requirements"]

        master_id = db.create_master_record(
            client_name="Client",
            technology=toc.get("title", "Unknown"),
            duration=toc.get("duration", ""),
            created_by=request.user_email,
            requirements=json.dumps(requirements)
        )

        version_id = db.create_version(
            master_id=master_id,
            toc_data=toc,
            version_number=1,
            regeneration_count=0
        )

        file_path = word_generator.generate(
            toc_data=toc,
            client_name=request.user_email.split("@")[0]
        )

        db.save_file_path(version_id, file_path)

        return {
            "master_id": master_id,
            "version_id": version_id,
            "version_number": 1,
            "status": "draft",
            "toc": toc,
            "file_path": file_path
        }

    except Exception as e:
        print("GENERATE ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# SME PENDING
# -----------------------------
@app.get("/sme/pending")
def get_pending_reviews():
    try:
        data = db.get_pending_reviews_with_details()

        return [
            {
                "version_id": v_id,
                "master_id": m_id,
                "version": ver,
                "client": client,
                "technology": tech,
                "date": created,
                "status": pending_review
            }
            for (v_id, m_id, ver, client, tech, created, pending_review) in data
        ]

    except Exception as e:
        print("SME PENDING ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# GET TOC + HISTORY
# -----------------------------
@app.get("/toc/{version_id}")
def get_toc(version_id: int):
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT v.toc_json, v.version_number, v.status,
                       v.feedback_notes, v.master_id, m.client_name
                FROM toc_versions v
                JOIN toc_master m ON v.master_id = m.id
                WHERE v.id = ?
            """, (version_id,))

            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="TOC not found")

            toc_json, version, status, feedback, master_id, client = row

            cursor.execute("""
                SELECT version_number, feedback_notes
                FROM toc_versions
                WHERE master_id = ?
                ORDER BY version_number
            """, (master_id,))

            history = [
                {"version": v, "feedback": f}
                for (v, f) in cursor.fetchall()
            ]

            return {
                "toc": normalize_toc(json.loads(toc_json)),
                "version": version,
                "status": status,
                "feedback": feedback,
                "client": client,
                "history": history
            }

    except HTTPException:
        raise
    except Exception as e:
        print("GET TOC ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# MY TOCs
# -----------------------------
@app.get("/my-tocs")
def get_my_tocs(email: str):
    try:
        data = db.get_tocs_by_user(email)

        return [
            {
                "version_id": v_id,
                "master_id": m_id,
                "version": version,
                "technology": tech,
                "client": client,
                "status": status,
                "created_at": created,
                "approved_at": approved_at,
                "date": get_display_date(created, approved_at)
            }
            for (v_id, m_id, version, tech, client, status, created, approved_at) in data
        ]

    except Exception as e:
        print("MY TOCS ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# DOWNLOAD
# -----------------------------
@app.get("/download/{version_id}")
def download_file(version_id: int):
    try:
        file_path = db.get_file_path(version_id)

        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")

        return FileResponse(
            path=file_path,
            filename=os.path.basename(file_path),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    except HTTPException:
        raise
    except Exception as e:
        print("DOWNLOAD ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# SEND TO SME
# -----------------------------
@app.post("/send-to-sme")
def send_to_sme(request: SendToSMERequest):
    try:
        version = db.get_full_version(request.version_id)

        if not version:
            raise HTTPException(status_code=404, detail="TOC not found")

        email_service.send_toc_for_review(
            version_id=request.version_id,
            sme_email=request.sme_email,
            toc_data=version["toc"],
            kam_email=request.kam_email
        )

        db.update_status(request.version_id, "pending_review")

        return {"message": "Sent to SME successfully"}

    except HTTPException:
        raise
    except Exception as e:
        print("SEND TO SME ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# APPROVE
# FIX: was missing set_approval_metadata call (feedback/approver not saved)
# -----------------------------
@app.post("/approve")
def approve_toc(request: ReviewRequest):
    try:
        db.update_status(request.version_id, "approved")

        # FIX: save approval metadata (was missing in Azure version)
        db.set_approval_metadata(
            request.version_id,
            approved_by="sme_user",
            feedback=request.feedback
        )

        version = db.get_full_version(request.version_id)
        toc_data = version["toc"]

        safe_title = re.sub(r'[^a-zA-Z0-9_]', '_', toc_data.get("title", "approved"))

        generated_path = word_generator.generate(
            toc_data=toc_data,
            client_name=f"{safe_title}_{request.version_id}"
        )

        BASE_DIR = Path(__file__).resolve().parent.parent
        approved_folder = BASE_DIR / "storage" / "approved"
        os.makedirs(approved_folder, exist_ok=True)

        approved_path = str(approved_folder / os.path.basename(generated_path))
        shutil.move(generated_path, approved_path)

        print(f"Approved DOCX moved to: {approved_path}")

        try:
            get_vector_service().index_document(approved_path)
            print("Indexing successful")
        except Exception as e:
            print("Indexing failed (non-fatal):", str(e))

        return {"message": "Approved"}

    except HTTPException:
        raise
    except Exception as e:
        print("APPROVE ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# REJECT
# -----------------------------
@app.post("/reject")
def reject_toc(request: ReviewRequest):
    try:
        if not request.feedback:
            raise HTTPException(status_code=400, detail="Feedback required")

        db.update_status(request.version_id, "rejected")

        db.set_approval_metadata(
            request.version_id,
            approved_by="sme_user",
            feedback=request.feedback
        )

        return {"message": "Rejected"}

    except HTTPException:
        raise
    except Exception as e:
        print("REJECT ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# REGENERATE
# FIX: orchestrator.run() returns {"toc": ..., "requirements": ...}
#      was passing full result dict to create_version instead of just toc
# -----------------------------
@app.post("/regenerate")
def regenerate(req: RegenerateRequest):
    try:
        old = db.get_full_version(req.version_id)

        if not old:
            raise HTTPException(status_code=404, detail="Version not found")

        feedback = old.get("feedback") or ""

        approved_tocs = db.get_approved_tocs()
        context = "\n\n".join([json.dumps(t, indent=2) for t in approved_tocs[:3]])

        improved_prompt = f"""
        You are an expert training content designer.

        Use these approved TOCs as reference:
        {context}

        Now improve the following TOC.

        Feedback:
        {feedback}

        Original TOC:
        {json.dumps(old['toc'], indent=2)}

        Requirements:
        - Make structure clearer
        - Improve beginner to advanced flow
        - Add missing important topics
        - Keep it practical and industry-ready
        """

        result = orchestrator.run(improved_prompt)

        if not result:
            raise HTTPException(status_code=500, detail="TOC generation failed")

        # FIX: extract just the toc dict from orchestrator result
        new_toc = result["toc"]

        new_version_id = db.create_version(
            master_id=old["master_id"],
            toc_data=new_toc,
            version_number=old["version"] + 1,
            regeneration_count=old["regeneration_count"] + 1
        )

        return {
            "message": "TOC regenerated successfully",
            "new_version_id": new_version_id
        }

    except HTTPException:
        raise
    except Exception as e:
        print("REGENERATE ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# SME STATS
# -----------------------------
@app.get("/sme/stats")
def get_sme_stats():
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN status = 'pending_review' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END)
                FROM toc_versions
            """)

            total, pending, approved, rejected = cursor.fetchone()

            return {
                "total": total or 0,
                "pending": pending or 0,
                "approved": approved or 0,
                "rejected": rejected or 0
            }

    except Exception as e:
        print("SME STATS ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# SME HISTORY
# -----------------------------
@app.get("/sme/history")
def get_sme_history():
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT v.id, m.technology, m.client_name,
                       v.version_number, v.status,
                       v.created_at, v.approved_at
                FROM toc_versions v
                JOIN toc_master m ON v.master_id = m.id
                WHERE v.status IN ('pending_review', 'approved', 'rejected')
                ORDER BY v.created_at DESC
            """)

            rows = cursor.fetchall()

            return [
                {
                    "version_id": r[0],
                    "technology": r[1],
                    "client": r[2],
                    "version": f"v{r[3]}",
                    "status": r[4],
                    "date": r[6] or r[5]
                }
                for r in rows
            ]

    except Exception as e:
        print("SME HISTORY ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# COMPARE
# -----------------------------
@app.get("/compare")
def compare_versions(v1_id: int, v2_id: int):
    try:
        v1 = db.get_full_version(v1_id)
        v2 = db.get_full_version(v2_id)

        if not v1 or not v2:
            raise HTTPException(status_code=404, detail="Version not found")

        def extract(toc):
            return set(
                t.strip().lower()
                for d in toc.get("days", [])
                for t in d.get("topics", [])
            )

        s1 = extract(v1["toc"])
        s2 = extract(v2["toc"])

        return {
            "added": list(s2 - s1),
            "removed": list(s1 - s2),
            "unchanged": list(s1 & s2)
        }

    except HTTPException:
        raise
    except Exception as e:
        print("COMPARE ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))