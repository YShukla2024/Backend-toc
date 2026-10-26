import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path


class DBManager:

    # Azure App Service deployment path
    BASE_DIR = Path("/home/site/wwwroot")
    DB_PATH = BASE_DIR / "data" / "toc_repository.db"

    def __init__(self):
        self.DB_PATH.parent.mkdir(exist_ok=True)
        self.initialize_database()
        self._ensure_columns()

    def get_connection(self):
        return sqlite3.connect(self.DB_PATH)

    # -----------------------------
    # INITIALIZE DATABASE
    # -----------------------------
    def initialize_database(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    email TEXT UNIQUE,
                    password TEXT,
                    role TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS toc_master (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_name TEXT NOT NULL,
                    technology TEXT NOT NULL,
                    duration_requested TEXT,
                    requirements TEXT,
                    created_by TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS toc_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    master_id INTEGER NOT NULL,
                    version_number INTEGER NOT NULL,
                    toc_json TEXT NOT NULL,
                    regeneration_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'draft',
                    approved_by TEXT,
                    approved_at TEXT,
                    feedback_notes TEXT,
                    embedding_status TEXT DEFAULT 'pending',
                    vector_id TEXT,
                    file_path TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(master_id) REFERENCES toc_master(id)
                )
            """)

            conn.commit()

    # -----------------------------
    # AUTO MIGRATION
    # -----------------------------
    def _ensure_columns(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("PRAGMA table_info(toc_versions)")
            columns = [col[1] for col in cursor.fetchall()]

            if "file_path" not in columns:
                cursor.execute("ALTER TABLE toc_versions ADD COLUMN file_path TEXT")
                conn.commit()

    # -----------------------------
    # VERSION CONTROL
    # -----------------------------
    def get_next_version_number(self, master_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT MAX(version_number)
                FROM toc_versions
                WHERE master_id = ?
            """, (master_id,))

            row = cursor.fetchone()
            max_version = row[0] if row and row[0] else 0
            return max_version + 1

    def create_version(self, master_id, toc_data, version_number=None, regeneration_count=0):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if version_number is None:
                version_number = self.get_next_version_number(master_id)

            cursor.execute("""
                INSERT INTO toc_versions
                (master_id, version_number, toc_json, regeneration_count, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                master_id,
                version_number,
                json.dumps(toc_data),
                regeneration_count,
                datetime.now(timezone.utc).isoformat()
            ))

            conn.commit()
            return cursor.lastrowid

    # -----------------------------
    # USER METHODS
    # -----------------------------
    def create_user(self, name, email, password, role):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO users (name, email, password, role)
                VALUES (?, ?, ?, ?)
            """, (name, email.lower(), password, role))

            conn.commit()

    def get_user_by_email(self, email):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT name, email, password, role
                FROM users
                WHERE LOWER(email) = ?
            """, (email.lower(),))

            row = cursor.fetchone()

            if row:
                return {
                    "name": row[0],
                    "email": row[1],
                    "password": row[2],
                    "role": row[3]
                }

            return None

    def authenticate_user(self, email, password):
        user = self.get_user_by_email(email)

        if user and user["password"] == password:
            return user["name"], user["role"]

        return None

    def update_password(self, email, new_password):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE users
                SET password = ?
                WHERE LOWER(email) = ?
            """, (new_password, email.lower()))

            conn.commit()

    # -----------------------------
    # MASTER
    # -----------------------------
    def create_master_record(self, client_name, technology, duration, created_by, requirements):
        if isinstance(requirements, dict):
            requirements = json.dumps(requirements)

        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO toc_master
                (client_name, technology, duration_requested, requirements, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                client_name,
                technology,
                duration,
                requirements,
                created_by,
                datetime.now(timezone.utc).isoformat()
            ))

            conn.commit()
            return cursor.lastrowid

    # -----------------------------
    # FILE
    # -----------------------------
    def save_file_path(self, version_id, file_path):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE toc_versions
                SET file_path = ?
                WHERE id = ?
            """, (file_path, version_id))

            conn.commit()

    def get_file_path(self, version_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT file_path
                FROM toc_versions
                WHERE id = ?
            """, (version_id,))

            row = cursor.fetchone()
            return row[0] if row else None

    # -----------------------------
    # STATUS
    # -----------------------------
    def update_status(self, version_id, status):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE toc_versions
                SET status = ?
                WHERE id = ?
            """, (status, version_id))

            conn.commit()

    def set_approval_metadata(self, version_id, approved_by, feedback=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE toc_versions
                SET approved_by = ?, approved_at = ?, feedback_notes = ?
                WHERE id = ?
            """, (
                approved_by,
                datetime.now(timezone.utc).isoformat(),
                feedback,
                version_id
            ))

            conn.commit()

    # -----------------------------
    # PENDING REVIEWS
    # FIX: was missing entirely — main.py calls this for /sme/pending
    # -----------------------------
    def get_pending_reviews_with_details(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT v.id, v.master_id, v.version_number,
                       m.client_name, m.technology, v.created_at, v.status
                FROM toc_versions v
                JOIN toc_master m ON v.master_id = m.id
                WHERE v.status = 'pending_review'
                ORDER BY v.created_at DESC
            """)

            return cursor.fetchall()

    # -----------------------------
    # SME
    # -----------------------------
    def get_sme_all_reviews(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT v.id, v.master_id, v.version_number,
                       m.client_name, m.technology, v.created_at, v.status
                FROM toc_versions v
                JOIN toc_master m ON v.master_id = m.id
                WHERE v.status IN ('pending_review', 'approved', 'rejected')
                ORDER BY v.created_at DESC
            """)

            return cursor.fetchall()

    def get_sme_stats(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    SUM(CASE WHEN status = 'pending_review' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END)
                FROM toc_versions
            """)

            row = cursor.fetchone()
            return {
                "pending": row[0] or 0,
                "approved": row[1] or 0,
                "rejected": row[2] or 0
            }

    # -----------------------------
    # HISTORY
    # -----------------------------
    def get_history_by_master(self, master_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT version_number, status, created_at
                FROM toc_versions
                WHERE master_id = ?
                AND status IN ('approved', 'rejected')
                ORDER BY version_number ASC
            """, (master_id,))

            return cursor.fetchall()

    # -----------------------------
    # USER VIEW
    # -----------------------------
    def get_tocs_by_user(self, email):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT v.id,
                       v.master_id,
                       v.version_number,
                       m.technology,
                       m.client_name,
                       v.status,
                       v.created_at,
                       v.approved_at
                FROM toc_versions v
                JOIN toc_master m ON v.master_id = m.id
                WHERE LOWER(m.created_by) = ?
                AND v.id IN (
                    SELECT MAX(id)
                    FROM toc_versions
                    GROUP BY master_id
                )
                ORDER BY v.created_at DESC
            """, (email.lower(),))

            return cursor.fetchall()

    # -----------------------------
    # GET FULL VERSION
    # -----------------------------
    def get_full_version(self, version_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, master_id, version_number, toc_json,
                       feedback_notes, regeneration_count
                FROM toc_versions
                WHERE id = ?
            """, (version_id,))

            row = cursor.fetchone()

            if not row:
                return None

            return {
                "id": row[0],
                "master_id": row[1],
                "version": row[2],
                "toc": json.loads(row[3]),
                "feedback": row[4],
                "regeneration_count": row[5]
            }

    # -----------------------------
    # GET TOC BY VERSION ID
    # FIX: was outside the class (zero indentation) — caused AttributeError at runtime
    # -----------------------------
    def get_toc_by_version(self, version_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT toc_json
                FROM toc_versions
                WHERE id = ?
            """, (version_id,))

            row = cursor.fetchone()

            if not row:
                return None

            return json.loads(row[0])

    # -----------------------------
    # APPROVED TOCs (RAG)
    # -----------------------------
    def get_approved_tocs(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT toc_json
                FROM toc_versions
                WHERE status = 'approved'
            """)

            rows = cursor.fetchall()
            return [json.loads(r[0]) for r in rows]