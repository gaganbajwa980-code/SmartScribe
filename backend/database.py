import json
import sqlite3
from datetime import datetime
from pathlib import Path

from config import DATABASE_PATH


class Database:

    def __init__(self, db_path=None):
        target_path = Path(db_path) if db_path else DATABASE_PATH
        target_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = str(target_path)
        self.connection = sqlite3.connect(
            self.db_path,
            check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

        self.create_tables()
        self.migrate_legacy_meetings()
        self.migrate_schema_v2()

    # =========================================================
    # SCHEMA & MIGRATIONS
    # =========================================================

    def create_tables(self):
        cursor = self.connection.cursor()

        # Legacy meetings table (maintained for full backwards compatibility)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                audio_file TEXT,
                transcript TEXT,
                analysis TEXT
            )
        """)

        # Core Workspace Items (Meetings, Lectures, Videos, Audios)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'meeting', -- 'meeting', 'lecture', 'video', 'audio'
                media_path TEXT,
                media_type TEXT,                      -- e.g. 'video/mp4', 'audio/wav'
                thumbnail_path TEXT,
                duration REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed', -- 'completed', 'processing', 'failed', 'cancelled'
                summary TEXT,
                key_points TEXT,
                action_items_text TEXT,
                topics TEXT,
                raw_analysis TEXT
            )
        """)

        # Timestamped Transcript Segments
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                speaker TEXT DEFAULT 'Speaker 1',
                text TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_segments_item ON segments(item_id)")

        # Timestamp-Preserving Transcript Chunks
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transcript_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                speaker TEXT,
                text TEXT NOT NULL,
                word_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_item ON transcript_chunks(item_id)")

        # Chapters / Timeline Markers
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                start_time REAL NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chapters_item ON chapters(item_id)")

        # Smart Bookmarks (Exam Points, Action Items, Key Quotes)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                tag TEXT NOT NULL DEFAULT 'important', -- 'important', 'exam', 'action', 'quote', 'question'
                title TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
        """)

        # User Notes (Freeform, Rich Notes linked to Sessions)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE SET NULL
            )
        """)

        # Action Items Checklist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS action_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                task TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                due_date TEXT,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
        """)

        # Study Materials (Flashcards, Quizzes, Exam Cram Sheets)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS study_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                type TEXT NOT NULL, -- 'flashcards', 'quiz', 'cram_sheet'
                title TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
        """)

        # Grounded AI Chat History per Item
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                role TEXT NOT NULL, -- 'user', 'assistant'
                content TEXT NOT NULL,
                citations_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
        """)

        # Background Processing Queue
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                stage TEXT NOT NULL DEFAULT 'queued',
                progress INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued', -- 'queued', 'running', 'completed', 'failed', 'cancelled'
                details TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        self.connection.commit()

    def migrate_legacy_meetings(self):
        """
        Migrates legacy meetings into the items and segments tables seamlessly.
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM items")
        items_count = cursor.fetchone()[0]

        if items_count > 0:
            return  # Already migrated

        cursor.execute("SELECT id, title, date, audio_file, transcript, analysis FROM meetings")
        legacy_rows = cursor.fetchall()

        for row in legacy_rows:
            m_id, title, date, audio_file, transcript, analysis = row
            created_at = date or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("""
                INSERT INTO items (
                    title, type, media_path, media_type, duration, created_at, status, summary, raw_analysis
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                title,
                "meeting",
                audio_file,
                "audio/wav" if audio_file and audio_file.endswith(".wav") else "audio/generic",
                0.0,
                created_at,
                "completed",
                analysis[:300] if analysis else "",
                analysis or ""
            ))
            item_id = cursor.lastrowid

            # Parse segments if transcript exists
            if transcript:
                segments = self._parse_transcript_lines(transcript)
                for seg in segments:
                    cursor.execute("""
                        INSERT INTO segments (item_id, start_time, end_time, speaker, text)
                        VALUES (?, ?, ?, ?, ?)
                    """, (item_id, seg["start"], seg["end"], "Speaker 1", seg["text"]))

        self.connection.commit()

    def migrate_schema_v2(self):
        """
        Creates SmartScribe 2.0 tables and adds columns for Smart Mode, Workspaces, and Speakers.
        """
        cursor = self.connection.cursor()

        # Workspaces table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                color TEXT DEFAULT '#6366f1',
                created_at TEXT NOT NULL
            )
        """)

        # Speakers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS speakers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                original_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT,
                color TEXT DEFAULT '#06b6d4',
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
                UNIQUE(item_id, original_name)
            )
        """)

        # Speaking Coach sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coach_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                duration REAL NOT NULL,
                word_count INTEGER NOT NULL,
                wpm REAL NOT NULL,
                pace_rating TEXT NOT NULL,
                filler_count INTEGER NOT NULL,
                filler_breakdown_json TEXT NOT NULL,
                feedback_json TEXT NOT NULL,
                transcript TEXT NOT NULL
            )
        """)

        # Add Smart Mode & Workspace columns to items if not present
        cursor.execute("PRAGMA table_info(items)")
        columns = [row["name"] for row in cursor.fetchall()]

        if "smart_mode" not in columns:
            cursor.execute("ALTER TABLE items ADD COLUMN smart_mode TEXT DEFAULT 'meeting'")
        if "smart_mode_confidence" not in columns:
            cursor.execute("ALTER TABLE items ADD COLUMN smart_mode_confidence TEXT DEFAULT 'high'")
        if "workspace_id" not in columns:
            cursor.execute("ALTER TABLE items ADD COLUMN workspace_id INTEGER")
        if "content_intelligence" not in columns:
            cursor.execute("ALTER TABLE items ADD COLUMN content_intelligence TEXT")

        self.connection.commit()

    # =========================================================
    # ITEM CRUD & QUERIES
    # =========================================================

    def create_item(
        self,
        title,
        item_type="meeting",
        media_path=None,
        media_type=None,
        thumbnail_path=None,
        duration=0.0,
        status="processing",
        summary="",
        raw_analysis=""
    ):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            INSERT INTO items (
                title, type, media_path, media_type, thumbnail_path, duration,
                created_at, status, summary, raw_analysis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            title, item_type, media_path, media_type, thumbnail_path,
            duration, now, status, summary, raw_analysis
        ))
        self.connection.commit()
        item_id = cursor.lastrowid

        # Also insert into legacy meetings table for backward compatibility
        try:
            cursor.execute("""
                INSERT INTO meetings (title, date, audio_file, transcript, analysis)
                VALUES (?, ?, ?, ?, ?)
            """, (title, now, media_path, "", raw_analysis))
            self.connection.commit()
        except Exception:
            pass

        return item_id

    def update_item(self, item_id, **kwargs):
        allowed_cols = {
            "title", "type", "media_path", "media_type", "thumbnail_path",
            "duration", "status", "summary", "key_points", "action_items_text",
            "topics", "raw_analysis", "smart_mode", "smart_mode_confidence",
            "workspace_id", "content_intelligence"
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_cols}
        if not updates:
            return

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        params = list(updates.values()) + [item_id]

        cursor = self.connection.cursor()
        cursor.execute(f"UPDATE items SET {set_clause} WHERE id = ?", params)
        self.connection.commit()

    def get_item(self, item_id):
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_all_items(self, item_type=None, limit=100, offset=0):
        cursor = self.connection.cursor()
        if item_type and item_type.lower() != "all":
            cursor.execute("""
                SELECT * FROM items
                WHERE type = ?
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """, (item_type.lower(), limit, offset))
        else:
            cursor.execute("""
                SELECT * FROM items
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
        return [dict(row) for row in cursor.fetchall()]

    get_items = get_all_items

    def search_items(self, query):
        if not query or not query.strip():
            return self.get_all_items()

        term = f"%{query.strip()}%"
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT DISTINCT i.*
            FROM items i
            LEFT JOIN segments s ON i.id = s.item_id
            WHERE i.title LIKE ?
               OR i.summary LIKE ?
               OR i.raw_analysis LIKE ?
               OR s.text LIKE ?
            ORDER BY i.id DESC
        """, (term, term, term, term))
        return [dict(row) for row in cursor.fetchall()]

    def delete_item(self, item_id):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
        # Also clean up legacy table if matched by ID or title
        try:
            cursor.execute("DELETE FROM meetings WHERE id = ?", (item_id,))
        except Exception:
            pass
        self.connection.commit()

    # =========================================================
    # SEGMENTS & TIMESTAMPS
    # =========================================================

    def save_segments(self, item_id, segments):
        """
        Replaces all segments for an item with the provided segment list.
        Each segment dict: {'start': float, 'end': float, 'text': str, 'speaker': str}
        """
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM segments WHERE item_id = ?", (item_id,))

        for seg in segments:
            cursor.execute("""
                INSERT INTO segments (item_id, start_time, end_time, speaker, text)
                VALUES (?, ?, ?, ?, ?)
            """, (
                item_id,
                float(seg.get("start", 0.0)),
                float(seg.get("end", 0.0)),
                seg.get("speaker", "Speaker 1"),
                seg.get("text", "").strip()
            ))
        self.connection.commit()

        # Update legacy meetings transcript text
        full_text = "\n".join([
            f"[{self._format_timestamp(s['start'])}] {s.get('text', '')}"
            for s in segments
        ])
        try:
            cursor.execute("UPDATE meetings SET transcript = ? WHERE id = ?", (full_text, item_id))
            self.connection.commit()
        except Exception:
            pass

    def get_segments(self, item_id):
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT id, item_id, start_time, end_time, speaker, text
            FROM segments
            WHERE item_id = ?
            ORDER BY start_time ASC
        """, (item_id,))
        return [dict(row) for row in cursor.fetchall()]

    def save_chunks(self, item_id, chunks):
        """
        Saves timestamp-preserving transcript chunks for an item.
        Each chunk dict: {chunk_index, start_time, end_time, speaker, text, word_count}
        """
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM transcript_chunks WHERE item_id = ?", (item_id,))
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for c in chunks:
            cursor.execute("""
                INSERT INTO transcript_chunks (
                    item_id, chunk_index, start_time, end_time, speaker, text, word_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item_id,
                int(c.get("chunk_index", 0)),
                float(c.get("start_time", 0.0)),
                float(c.get("end_time", 0.0)),
                c.get("speaker", "Speaker 1"),
                c.get("text", "").strip(),
                int(c.get("word_count", 0)),
                now_str
            ))
        self.connection.commit()

    def get_chunks(self, item_id):
        """
        Retrieves all chunks for an item ordered by chunk_index.
        """
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT id, item_id, chunk_index, start_time, end_time, speaker, text, word_count, created_at
            FROM transcript_chunks
            WHERE item_id = ?
            ORDER BY chunk_index ASC
        """, (item_id,))
        rows = cursor.fetchall()
        from backend.chunker import format_timestamp
        results = []
        for r in rows:
            d = dict(r)
            d["chunk_id"] = f"chunk_{d['chunk_index'] + 1}"
            d["start_timestamp"] = format_timestamp(d["start_time"])
            d["end_timestamp"] = format_timestamp(d["end_time"])
            results.append(d)
        return results

    # =========================================================
    # CHAPTERS & TIMELINE
    # =========================================================

    def save_chapters(self, item_id, chapters):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM chapters WHERE item_id = ?", (item_id,))

        for ch in chapters:
            cursor.execute("""
                INSERT INTO chapters (item_id, start_time, title, description)
                VALUES (?, ?, ?, ?)
            """, (item_id, float(ch.get("start_time", 0.0)), ch.get("title", ""), ch.get("description", "")))
        self.connection.commit()

    def get_chapters(self, item_id):
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT id, item_id, start_time, title, description
            FROM chapters
            WHERE item_id = ?
            ORDER BY start_time ASC
        """, (item_id,))
        return [dict(row) for row in cursor.fetchall()]

    # =========================================================
    # BOOKMARKS
    # =========================================================

    def add_bookmark(self, item_id, timestamp, tag, title, note=""):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO bookmarks (item_id, timestamp, tag, title, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (item_id, float(timestamp), tag, title, note, now))
        self.connection.commit()
        return cursor.lastrowid

    def get_bookmarks(self, item_id):
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM bookmarks
            WHERE item_id = ?
            ORDER BY timestamp ASC
        """, (item_id,))
        return [dict(row) for row in cursor.fetchall()]

    def delete_bookmark(self, bookmark_id):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
        self.connection.commit()

    # =========================================================
    # NOTES
    # =========================================================

    def create_note(self, item_id, title, content, tags=""):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO notes (item_id, title, content, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (item_id, title, content, tags, now, now))
        self.connection.commit()
        return cursor.lastrowid

    def update_note(self, note_id, title, content, tags=""):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE notes
            SET title = ?, content = ?, tags = ?, updated_at = ?
            WHERE id = ?
        """, (title, content, tags, now, note_id))
        self.connection.commit()

    def get_notes(self, item_id=None):
        cursor = self.connection.cursor()
        if item_id:
            cursor.execute("""
                SELECT * FROM notes
                WHERE item_id = ?
                ORDER BY updated_at DESC
            """, (item_id,))
        else:
            cursor.execute("SELECT * FROM notes ORDER BY updated_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def delete_note(self, note_id):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.connection.commit()

    # =========================================================
    # ACTION ITEMS
    # =========================================================

    def add_action_item(self, item_id, task, completed=0, due_date=None):
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO action_items (item_id, task, completed, due_date)
            VALUES (?, ?, ?, ?)
        """, (item_id, task, completed, due_date))
        self.connection.commit()
        return cursor.lastrowid

    def toggle_action_item(self, action_id, completed):
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE action_items
            SET completed = ?
            WHERE id = ?
        """, (1 if completed else 0, action_id))
        self.connection.commit()

    def get_action_items(self, item_id=None):
        cursor = self.connection.cursor()
        if item_id:
            cursor.execute("""
                SELECT * FROM action_items
                WHERE item_id = ?
                ORDER BY id ASC
            """, (item_id,))
        else:
            cursor.execute("SELECT * FROM action_items ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]

    # =========================================================
    # STUDY MATERIALS (Flashcards & Quizzes)
    # =========================================================

    def save_study_material(self, item_id, mat_type, title, data_obj):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data_json = json.dumps(data_obj) if isinstance(data_obj, (dict, list)) else str(data_obj)

        cursor.execute("""
            INSERT INTO study_materials (item_id, type, title, data_json, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (item_id, mat_type, title, data_json, now))
        self.connection.commit()
        return cursor.lastrowid

    def get_study_materials(self, item_id, mat_type=None):
        cursor = self.connection.cursor()
        if mat_type:
            cursor.execute("""
                SELECT * FROM study_materials
                WHERE item_id = ? AND type = ?
                ORDER BY id DESC
            """, (item_id, mat_type))
        else:
            cursor.execute("""
                SELECT * FROM study_materials
                WHERE item_id = ?
                ORDER BY id DESC
            """, (item_id,))

        results = []
        for row in cursor.fetchall():
            d = dict(row)
            try:
                d["data"] = json.loads(d["data_json"])
            except Exception:
                d["data"] = d["data_json"]
            results.append(d)
        return results

    # =========================================================
    # GROUNDED AI CHAT MESSAGES
    # =========================================================

    def save_chat_message(self, item_id, role, content, citations=None):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cit_json = json.dumps(citations or [])
        cursor.execute("""
            INSERT INTO chat_messages (item_id, role, content, citations_json, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (item_id, role, content, cit_json, now))
        self.connection.commit()
        return cursor.lastrowid

    def get_chat_messages(self, item_id):
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM chat_messages
            WHERE item_id = ?
            ORDER BY id ASC
        """, (item_id,))
        res = []
        for row in cursor.fetchall():
            d = dict(row)
            try:
                d["citations"] = json.loads(d["citations_json"])
            except Exception:
                d["citations"] = []
            res.append(d)
        return res

    # =========================================================
    # BACKGROUND JOBS
    # =========================================================

    def create_job(self, item_id=None, stage="queued", details=""):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO jobs (item_id, stage, progress, status, details, created_at, updated_at)
            VALUES (?, ?, 0, 'queued', ?, ?, ?)
        """, (item_id, stage, details, now, now))
        self.connection.commit()
        return cursor.lastrowid

    def update_job(self, job_id, stage=None, progress=None, status=None, details=None, error_message=None):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        fields = ["updated_at = ?"]
        params = [now]

        if stage is not None:
            fields.append("stage = ?")
            params.append(stage)
        if progress is not None:
            fields.append("progress = ?")
            params.append(progress)
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if details is not None:
            fields.append("details = ?")
            params.append(details)
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(error_message)

        params.append(job_id)
        cursor.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", params)
        self.connection.commit()

    def get_jobs(self, limit=20):
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

    # =========================================================
    # BACKWARD-COMPATIBLE LEGACY METHODS
    # =========================================================

    def save_meeting(self, title, audio_file, transcript, analysis):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO meetings (title, date, audio_file, transcript, analysis)
            VALUES (?, ?, ?, ?, ?)
        """, (title, now, audio_file, transcript, analysis))
        self.connection.commit()
        legacy_id = cursor.lastrowid

        # Also create item
        item_id = self.create_item(
            title=title,
            item_type="meeting",
            media_path=audio_file,
            media_type="audio/wav",
            status="completed",
            summary=analysis[:300] if analysis else "",
            raw_analysis=analysis or ""
        )

        if transcript:
            segments = self._parse_transcript_lines(transcript)
            self.save_segments(item_id, segments)

        return legacy_id

    def get_all_meetings(self):
        cursor = self.connection.cursor()
        cursor.execute("SELECT id, title, date FROM meetings ORDER BY id DESC")
        return [tuple(row) for row in cursor.fetchall()]

    def search_meetings(self, query):
        if not query or not query.strip():
            return self.get_all_meetings()

        term = f"%{query.strip()}%"
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT id, title, date
            FROM meetings
            WHERE title LIKE ? OR transcript LIKE ? OR analysis LIKE ?
            ORDER BY id DESC
        """, (term, term, term))
        return [tuple(row) for row in cursor.fetchall()]

    def get_meeting(self, meeting_id):
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        row = cursor.fetchone()
        return tuple(row) if row else None

    def delete_meeting(self, meeting_id):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        self.connection.commit()

    # =========================================================
    # SPEAKER MANAGEMENT
    # =========================================================

    def rename_speaker(self, item_id, old_name, new_name, role=None, color=None):
        """
        Renames a speaker across all segments of an item and records in speakers table.
        """
        cursor = self.connection.cursor()
        # Update segments table
        cursor.execute("""
            UPDATE segments SET speaker = ? WHERE item_id = ? AND speaker = ?
        """, (new_name, item_id, old_name))

        # Upsert into speakers table
        cursor.execute("""
            INSERT INTO speakers (item_id, original_name, display_name, role, color)
            VALUES (?, ?, ?, ?, COALESCE(?, '#06b6d4'))
            ON CONFLICT(item_id, original_name) DO UPDATE SET
                display_name = excluded.display_name,
                role = COALESCE(excluded.role, speakers.role),
                color = COALESCE(excluded.color, speakers.color)
        """, (item_id, old_name, new_name, role, color))

        self.connection.commit()
        return True

    def get_item_speakers(self, item_id):
        """
        Returns all unique speakers for an item with segment counts and roles.
        """
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT s.speaker, COUNT(*) as segment_count,
                   sp.display_name, sp.role, sp.color
            FROM segments s
            LEFT JOIN speakers sp ON s.item_id = sp.item_id AND s.speaker = sp.display_name
            WHERE s.item_id = ?
            GROUP BY s.speaker
            ORDER BY segment_count DESC
        """, (item_id,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

    # =========================================================
    # GLOBAL "FIND IT" DEEP SEARCH
    # =========================================================

    def search_deep(self, query, item_type=None, workspace_id=None, limit=40):
        """
        Searches across transcripts, summaries, topics, notes, and action items.
        Returns grouped, rich search results with exact timestamp context.
        """
        if not query or not query.strip():
            return {"segments": [], "items": [], "notes": []}

        q = f"%{query.strip().lower()}%"
        cursor = self.connection.cursor()

        # 1. Search in Transcript Segments
        seg_sql = """
            SELECT s.id, s.item_id, s.start_time, s.end_time, s.speaker, s.text,
                   i.title as item_title, i.type as item_type, i.smart_mode
            FROM segments s
            JOIN items i ON s.item_id = i.id
            WHERE LOWER(s.text) LIKE ?
        """
        params = [q]
        if item_type and item_type.lower() != "all":
            seg_sql += " AND (i.type = ? OR i.smart_mode = ?)"
            params.extend([item_type.lower(), item_type.lower()])
        if workspace_id:
            seg_sql += " AND i.workspace_id = ?"
            params.append(workspace_id)

        seg_sql += " ORDER BY s.id DESC LIMIT ?"
        params.append(limit)

        cursor.execute(seg_sql, params)
        segment_matches = [dict(r) for r in cursor.fetchall()]

        # 2. Search in Items (Title, Summary, Key Points, Topics)
        item_sql = """
            SELECT id, title, type, smart_mode, duration, created_at, summary, topics
            FROM items
            WHERE (LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(key_points) LIKE ? OR LOWER(topics) LIKE ?)
        """
        item_params = [q, q, q, q]
        if item_type and item_type.lower() != "all":
            item_sql += " AND (type = ? OR smart_mode = ?)"
            item_params.extend([item_type.lower(), item_type.lower()])
        if workspace_id:
            item_sql += " AND workspace_id = ?"
            item_params.append(workspace_id)

        item_sql += " ORDER BY id DESC LIMIT 15"
        cursor.execute(item_sql, item_params)
        item_matches = [dict(r) for r in cursor.fetchall()]

        # 3. Search in Notes
        cursor.execute("""
            SELECT n.id, n.item_id, n.title, n.content, n.created_at, i.title as item_title
            FROM notes n
            LEFT JOIN items i ON n.item_id = i.id
            WHERE LOWER(n.title) LIKE ? OR LOWER(n.content) LIKE ?
            ORDER BY n.id DESC LIMIT 15
        """, (q, q))
        note_matches = [dict(r) for r in cursor.fetchall()]

        return {
            "query": query.strip(),
            "segments": segment_matches,
            "items": item_matches,
            "notes": note_matches,
            "total_count": len(segment_matches) + len(item_matches) + len(note_matches)
        }

    # =========================================================
    # WORKSPACES CRUD
    # =========================================================

    def create_workspace(self, name, description="", color="#6366f1"):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO workspaces (name, description, color, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description = excluded.description,
                color = excluded.color
        """, (name, description, color, now))
        self.connection.commit()
        if cursor.lastrowid:
            return cursor.lastrowid
        cursor.execute("SELECT id FROM workspaces WHERE name = ?", (name,))
        row = cursor.fetchone()
        return row[0] if row else None

    def get_all_workspaces(self):
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT w.*, COUNT(i.id) as item_count
            FROM workspaces w
            LEFT JOIN items i ON w.id = i.workspace_id
            GROUP BY w.id
            ORDER BY w.id ASC
        """)
        return [dict(r) for r in cursor.fetchall()]

    def get_workspace(self, workspace_id):
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_workspace(self, workspace_id):
        cursor = self.connection.cursor()
        cursor.execute("UPDATE items SET workspace_id = NULL WHERE workspace_id = ?", (workspace_id,))
        cursor.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        self.connection.commit()
        return True

    def assign_item_workspace(self, item_id, workspace_id):
        cursor = self.connection.cursor()
        cursor.execute("UPDATE items SET workspace_id = ? WHERE id = ?", (workspace_id, item_id))
        self.connection.commit()
        return True

    # =========================================================
    # SPEAKING COACH SESSIONS
    # =========================================================

    def save_coach_session(
        self,
        title,
        duration,
        word_count,
        wpm,
        pace_rating,
        filler_count,
        filler_breakdown,
        feedback,
        transcript
    ):
        cursor = self.connection.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO coach_sessions (
                title, created_at, duration, word_count, wpm, pace_rating,
                filler_count, filler_breakdown_json, feedback_json, transcript
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            title, now, duration, word_count, wpm, pace_rating,
            filler_count, json.dumps(filler_breakdown), json.dumps(feedback), transcript
        ))
        self.connection.commit()
        return cursor.lastrowid

    def get_coach_sessions(self, limit=30):
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM coach_sessions ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["filler_breakdown"] = json.loads(d.get("filler_breakdown_json") or "{}")
                d["feedback"] = json.loads(d.get("feedback_json") or "{}")
            except Exception:
                d["filler_breakdown"] = {}
                d["feedback"] = {}
            result.append(d)
        return result

    def get_coach_session(self, session_id):
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM coach_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["filler_breakdown"] = json.loads(d.get("filler_breakdown_json") or "{}")
            d["feedback"] = json.loads(d.get("feedback_json") or "{}")
        except Exception:
            d["filler_breakdown"] = {}
            d["feedback"] = {}
        return d

    def delete_coach_session(self, session_id):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM coach_sessions WHERE id = ?", (session_id,))
        self.connection.commit()
        return True

    def close(self):
        try:
            self.connection.close()
        except Exception:
            pass

    # =========================================================
    # INTERNAL HELPERS
    # =========================================================

    @staticmethod
    def _format_timestamp(seconds):
        sec = int(seconds)
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    @staticmethod
    def _parse_transcript_lines(text):
        segments = []
        for line in text.splitlines():
            line = line.strip()
            if not line or "]" not in line:
                continue
            try:
                time_part = line.split("]")[0].replace("[", "").strip()
                content = line.split("]", 1)[1].strip()
                parts = time_part.split(":")
                if len(parts) == 2:
                    secs = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                else:
                    continue
                segments.append({
                    "start": float(secs),
                    "end": float(secs + 2.0),
                    "text": content,
                    "speaker": "Speaker 1"
                })
            except Exception:
                continue
        return segments