import os
from contextlib import contextmanager
from datetime import timedelta, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL is required. Connect a PostgreSQL database in Render and set DATABASE_URL.')

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(30) NOT NULL,
    username_normalized VARCHAR(30) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subjects (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id BIGINT REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT REFERENCES chapters(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pdf_files (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id BIGINT REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT REFERENCES chapters(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    data BYTEA NOT NULL,
    file_type VARCHAR(10) NOT NULL DEFAULT 'pdf',
    preview_data BYTEA NULL,
    preview_page_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pdf_pages (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pdf_id BIGINT NOT NULL REFERENCES pdf_files(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subjects_user ON subjects(user_id);
CREATE INDEX IF NOT EXISTS idx_chapters_user ON chapters(user_id);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_pdf_user ON pdf_files(user_id);
CREATE INDEX IF NOT EXISTS idx_pdf_pages_user ON pdf_pages(user_id);
CREATE TABLE IF NOT EXISTS audio_notes (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note_id BIGINT REFERENCES notes(id) ON DELETE CASCADE,
    subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT REFERENCES chapters(id) ON DELETE CASCADE,
    mime_type TEXT NOT NULL DEFAULT 'audio/webm',
    data BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audio_notes_user ON audio_notes(user_id);
CREATE TABLE IF NOT EXISTS sticky_notes (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT REFERENCES chapters(id) ON DELETE CASCADE,
    pdf_id BIGINT REFERENCES pdf_files(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    color VARCHAR(20) NOT NULL DEFAULT 'violet',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sticky_notes_user ON sticky_notes(user_id);
CREATE INDEX IF NOT EXISTS idx_sticky_notes_pdf ON sticky_notes(pdf_id);
CREATE TABLE IF NOT EXISTS study_quizzes (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT REFERENCES chapters(id) ON DELETE CASCADE,
    questions JSONB NOT NULL,
    difficulty VARCHAR(12) NOT NULL DEFAULT 'medium',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id BIGSERIAL PRIMARY KEY,
    quiz_id BIGINT NOT NULL REFERENCES study_quizzes(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT REFERENCES chapters(id) ON DELETE SET NULL,
    total_questions INTEGER NOT NULL,
    correct_answers INTEGER NOT NULL,
    percentage NUMERIC(5,2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_user_date ON quiz_attempts(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS revision_flashcards (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT REFERENCES chapters(id) ON DELETE CASCADE,
    cards JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_revision_flashcards_user ON revision_flashcards(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS study_plan_tasks (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT REFERENCES chapters(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    task_type VARCHAR(24) NOT NULL DEFAULT 'revise',
    due_date DATE NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 25,
    priority VARCHAR(12) NOT NULL DEFAULT 'normal',
    rationale TEXT NOT NULL DEFAULT '',
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_study_plan_tasks_user_date ON study_plan_tasks(user_id, due_date, completed);
CREATE TABLE IF NOT EXISTS study_plan_programs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'My study plan',
    start_date DATE NOT NULL,
    days INTEGER NOT NULL DEFAULT 3,
    daily_minutes INTEGER NOT NULL DEFAULT 120,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS study_plan_program_chapters (
    id BIGSERIAL PRIMARY KEY,
    program_id BIGINT NOT NULL REFERENCES study_plan_programs(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(program_id, chapter_id)
);
ALTER TABLE study_plan_tasks ADD COLUMN IF NOT EXISTS program_id BIGINT REFERENCES study_plan_programs(id) ON DELETE SET NULL;
ALTER TABLE study_plan_tasks ADD COLUMN IF NOT EXISTS schedule_day INTEGER NULL;
ALTER TABLE study_plan_tasks ADD COLUMN IF NOT EXISTS scheduled_time TIME NULL;
CREATE INDEX IF NOT EXISTS idx_study_plan_tasks_program ON study_plan_tasks(program_id, schedule_day, completed);
CREATE TABLE IF NOT EXISTS study_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id BIGINT NULL REFERENCES study_plan_tasks(id) ON DELETE SET NULL,
    subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id BIGINT NULL REFERENCES chapters(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT 'Study session',
    planned_minutes INTEGER NOT NULL DEFAULT 25,
    elapsed_seconds INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'running',
    running_since TIMESTAMPTZ NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_study_sessions_user_started ON study_sessions(user_id, started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_study_timer_per_user ON study_sessions(user_id) WHERE status IN ('running','paused');
CREATE TABLE IF NOT EXISTS study_planner_preferences (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    daily_target_minutes INTEGER NOT NULL DEFAULT 180,
    preferred_session_minutes INTEGER NOT NULL DEFAULT 50,
    break_minutes INTEGER NOT NULL DEFAULT 10,
    break_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    availability_start TIME NULL,
    availability_end TIME NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE pdf_files ADD COLUMN IF NOT EXISTS file_type VARCHAR(10) NOT NULL DEFAULT 'pdf';
ALTER TABLE pdf_files ADD COLUMN IF NOT EXISTS preview_data BYTEA NULL;
ALTER TABLE pdf_files ADD COLUMN IF NOT EXISTS preview_page_count INTEGER NOT NULL DEFAULT 0;
"""

@contextmanager
def connect():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def init_db():
    with connect() as conn:
        conn.execute(SCHEMA)
        # Phase 4 migration: every material belongs to a subject; chapter is optional.
        conn.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS subject_id BIGINT REFERENCES subjects(id) ON DELETE CASCADE")
        conn.execute("ALTER TABLE pdf_files ADD COLUMN IF NOT EXISTS subject_id BIGINT REFERENCES subjects(id) ON DELETE CASCADE")
        conn.execute("UPDATE notes n SET subject_id=c.subject_id FROM chapters c WHERE n.chapter_id=c.id AND n.subject_id IS NULL")
        conn.execute("UPDATE pdf_files p SET subject_id=c.subject_id FROM chapters c WHERE p.chapter_id=c.id AND p.subject_id IS NULL")
        conn.execute("ALTER TABLE notes ALTER COLUMN chapter_id DROP NOT NULL")
        conn.execute("ALTER TABLE pdf_files ALTER COLUMN chapter_id DROP NOT NULL")
        conn.execute("ALTER TABLE notes ALTER COLUMN subject_id SET NOT NULL")
        conn.execute("ALTER TABLE pdf_files ALTER COLUMN subject_id SET NOT NULL")

# ---------- users / authentication ----------
def get_user_by_username(username):
    with connect() as conn:
        return conn.execute('SELECT * FROM users WHERE username_normalized=%s', (username.strip().lower(),)).fetchone()


def get_user(user_id):
    with connect() as conn:
        return conn.execute('SELECT id, username, created_at FROM users WHERE id=%s', (user_id,)).fetchone()


def create_user(username, password_hash):
    u = username.strip()
    with connect() as conn:
        return conn.execute(
            'INSERT INTO users(username,username_normalized,password_hash) VALUES(%s,%s,%s) RETURNING id,username',
            (u, u.lower(), password_hash)
        ).fetchone()


def record_failed_login(user_id, attempts, locked_until):
    with connect() as conn:
        conn.execute('UPDATE users SET failed_attempts=%s, locked_until=%s WHERE id=%s', (attempts, locked_until, user_id))


def reset_login_failures(user_id):
    with connect() as conn:
        conn.execute('UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=%s', (user_id,))

def change_password(user_id, password_hash):
    with connect() as conn:
        conn.execute('UPDATE users SET password_hash=%s, failed_attempts=0, locked_until=NULL WHERE id=%s', (password_hash, user_id))

# ---------- dashboard / study data ----------
def dashboard_data(user_id):
    with connect() as conn:
        subjects = conn.execute('''
            SELECT s.id, s.name, COUNT(c.id)::int AS chapter_count
            FROM subjects s LEFT JOIN chapters c ON c.subject_id=s.id AND c.user_id=s.user_id
            WHERE s.user_id=%s GROUP BY s.id ORDER BY s.name COLLATE "C"
        ''', (user_id,)).fetchall()
        total_chapters = conn.execute('SELECT COUNT(*) FROM chapters WHERE user_id=%s', (user_id,)).fetchone()['count']
        total_notes = conn.execute('SELECT COUNT(*) FROM notes WHERE user_id=%s', (user_id,)).fetchone()['count']
        total_pdfs = conn.execute('SELECT COUNT(*) FROM pdf_files WHERE user_id=%s', (user_id,)).fetchone()['count']
        chapters = conn.execute('''
            SELECT c.id,c.name,c.subject_id,s.name AS subject_name
            FROM chapters c JOIN subjects s ON s.id=c.subject_id
            WHERE c.user_id=%s AND s.user_id=%s
            ORDER BY s.name COLLATE "C", c.name COLLATE "C"
        ''', (user_id, user_id)).fetchall()
    return subjects, chapters, {'chapters': total_chapters, 'notes': total_notes, 'pdfs': total_pdfs}


def get_subject(user_id, subject_id):
    with connect() as conn:
        return conn.execute('SELECT id,name,user_id FROM subjects WHERE id=%s AND user_id=%s', (subject_id,user_id)).fetchone()


def list_chapters(user_id, subject_id):
    with connect() as conn:
        return conn.execute('''
            SELECT c.id,c.name,c.subject_id,
              (SELECT COUNT(*) FROM notes n WHERE n.chapter_id=c.id AND n.user_id=%s) AS note_count,
              (SELECT COUNT(*) FROM pdf_files p WHERE p.chapter_id=c.id AND p.user_id=%s) AS pdf_count
            FROM chapters c WHERE c.subject_id=%s AND c.user_id=%s
            ORDER BY c.name COLLATE "C"
        ''', (user_id,user_id,subject_id,user_id)).fetchall()

def list_subject_notes(user_id, subject_id):
    with connect() as conn:
        return conn.execute('''SELECT n.id,n.title,n.body,n.subject_id,n.chapter_id,a.id AS audio_id FROM notes n LEFT JOIN audio_notes a ON a.note_id=n.id WHERE n.subject_id=%s AND n.chapter_id IS NULL AND n.user_id=%s ORDER BY n.id DESC''', (subject_id,user_id)).fetchall()

def list_subject_pdfs(user_id, subject_id):
    with connect() as conn:
        return conn.execute('''SELECT p.id,p.original_name,p.page_count,p.file_type,p.created_at,COUNT(pg.id)::int AS text_pages FROM pdf_files p LEFT JOIN pdf_pages pg ON pg.pdf_id=p.id AND pg.user_id=%s WHERE p.subject_id=%s AND p.chapter_id IS NULL AND p.user_id=%s GROUP BY p.id ORDER BY p.id DESC''', (user_id,subject_id,user_id)).fetchall()


def list_all_chapters(user_id):
    with connect() as conn:
        return conn.execute('''SELECT c.id,c.name,c.subject_id,s.name AS subject_name FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.user_id=%s AND s.user_id=%s ORDER BY s.name,c.name''', (user_id,user_id)).fetchall()


def add_subject(user_id, name):
    with connect() as conn:
        return conn.execute('INSERT INTO subjects(user_id,name) VALUES(%s,%s) RETURNING id', (user_id,name)).fetchone()['id']


def rename_subject(user_id, subject_id, name):
    with connect() as conn:
        conn.execute('UPDATE subjects SET name=%s WHERE id=%s AND user_id=%s', (name,subject_id,user_id))


def delete_subject(user_id, subject_id):
    with connect() as conn:
        conn.execute('DELETE FROM subjects WHERE id=%s AND user_id=%s', (subject_id,user_id))


def get_chapter(user_id, chapter_id):
    with connect() as conn:
        return conn.execute('''SELECT c.id,c.name,c.subject_id,c.user_id,s.name AS subject_name FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.id=%s AND c.user_id=%s AND s.user_id=%s''', (chapter_id,user_id,user_id)).fetchone()


def add_chapter(user_id, subject_id, name):
    with connect() as conn:
        return conn.execute('INSERT INTO chapters(user_id,subject_id,name) SELECT %s,id,%s FROM subjects WHERE id=%s AND user_id=%s RETURNING id', (user_id,name,subject_id,user_id)).fetchone()


def rename_chapter(user_id, chapter_id, name):
    with connect() as conn:
        conn.execute('UPDATE chapters SET name=%s WHERE id=%s AND user_id=%s', (name,chapter_id,user_id))


def delete_chapter(user_id, chapter_id):
    with connect() as conn:
        conn.execute('DELETE FROM chapters WHERE id=%s AND user_id=%s', (chapter_id,user_id))


def update_note(user_id, note_id, title, body):
    with connect() as conn:
        conn.execute('UPDATE notes SET title=%s, body=%s WHERE id=%s AND user_id=%s', (title,body,note_id,user_id))


def list_notes(user_id, chapter_id):
    with connect() as conn:
        return conn.execute('SELECT n.id,n.title,n.body,n.chapter_id,a.id AS audio_id FROM notes n LEFT JOIN audio_notes a ON a.note_id=n.id WHERE n.chapter_id=%s AND n.user_id=%s ORDER BY n.id DESC', (chapter_id,user_id)).fetchall()


def get_note(user_id, note_id):
    with connect() as conn:
        return conn.execute('SELECT n.*,a.id AS audio_id FROM notes n LEFT JOIN audio_notes a ON a.note_id=n.id WHERE n.id=%s AND n.user_id=%s', (note_id,user_id)).fetchone()


def add_note(user_id, subject_id, chapter_id, title, body):
    with connect() as conn:
        if chapter_id is None:
            return conn.execute('INSERT INTO notes(user_id,subject_id,chapter_id,title,body) SELECT %s,id,NULL,%s,%s FROM subjects WHERE id=%s AND user_id=%s RETURNING id', (user_id,title,body,subject_id,user_id)).fetchone()
        return conn.execute('INSERT INTO notes(user_id,subject_id,chapter_id,title,body) SELECT %s,s.id,c.id,%s,%s FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.id=%s AND c.user_id=%s AND s.user_id=%s AND s.id=%s RETURNING id', (user_id,title,body,chapter_id,user_id,user_id,subject_id)).fetchone()


def delete_note(user_id, note_id):
    with connect() as conn:
        conn.execute('DELETE FROM notes WHERE id=%s AND user_id=%s', (note_id,user_id))


def add_pdf(user_id, subject_id, chapter_id, original_name, data, page_count, pages, file_type='pdf', preview_data=None, preview_page_count=0):
    with connect() as conn:
        if chapter_id is None:
            row = conn.execute('INSERT INTO pdf_files(user_id,subject_id,chapter_id,original_name,page_count,data,file_type,preview_data,preview_page_count) SELECT %s,id,NULL,%s,%s,%s,%s,%s,%s FROM subjects WHERE id=%s AND user_id=%s RETURNING id', (user_id,original_name,page_count,data,file_type,preview_data,preview_page_count,subject_id,user_id)).fetchone()
        else:
            row = conn.execute('INSERT INTO pdf_files(user_id,subject_id,chapter_id,original_name,page_count,data,file_type,preview_data,preview_page_count) SELECT %s,s.id,c.id,%s,%s,%s,%s,%s,%s FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.id=%s AND c.user_id=%s AND s.user_id=%s AND s.id=%s RETURNING id', (user_id,original_name,page_count,data,file_type,preview_data,preview_page_count,chapter_id,user_id,user_id,subject_id)).fetchone()
        if not row:
            return None
        pdf_id = row['id']
        with conn.cursor() as cur:
            cur.executemany('INSERT INTO pdf_pages(user_id,pdf_id,page_number,text) VALUES(%s,%s,%s,%s)', [(user_id,pdf_id,n,t) for n,t in pages])
        return pdf_id


def list_pdfs(user_id, chapter_id):
    with connect() as conn:
        return conn.execute('''SELECT p.id,p.original_name,p.page_count,p.file_type,p.created_at,COUNT(pg.id)::int AS text_pages FROM pdf_files p LEFT JOIN pdf_pages pg ON pg.pdf_id=p.id AND pg.user_id=%s WHERE p.chapter_id=%s AND p.user_id=%s GROUP BY p.id ORDER BY p.id DESC''', (user_id,chapter_id,user_id)).fetchall()


def get_pdf(user_id, pdf_id, include_data=False):
    with connect() as conn:
        cols='p.id,p.original_name,p.page_count,p.file_type,p.subject_id,p.chapter_id,p.user_id' + (',p.data' if include_data else '')
        return conn.execute(f'''SELECT {cols} FROM pdf_files p WHERE p.id=%s AND p.user_id=%s''', (pdf_id,user_id)).fetchone()


def get_file_preview(user_id, pdf_id):
    with connect() as conn:
        return conn.execute('SELECT preview_data,preview_page_count,file_type,original_name FROM pdf_files WHERE id=%s AND user_id=%s', (pdf_id,user_id)).fetchone()

def save_file_preview(user_id, pdf_id, preview_data, preview_page_count):
    with connect() as conn:
        conn.execute('UPDATE pdf_files SET preview_data=%s, preview_page_count=%s WHERE id=%s AND user_id=%s', (preview_data,preview_page_count,pdf_id,user_id))

def get_pdf_pages(user_id, pdf_id):
    with connect() as conn:
        return conn.execute('SELECT page_number,text FROM pdf_pages WHERE pdf_id=%s AND user_id=%s ORDER BY page_number', (pdf_id,user_id)).fetchall()


def delete_pdf(user_id, pdf_id):
    with connect() as conn:
        conn.execute('DELETE FROM pdf_files WHERE id=%s AND user_id=%s', (pdf_id,user_id))


def add_audio_note(user_id, subject_id, chapter_id, title, transcript, data, mime_type='audio/webm'):
    # Keep the note row and its audio row in one PostgreSQL transaction so a
    # voice note is either saved completely or not saved at all.
    with connect() as conn:
        if chapter_id is None:
            note = conn.execute(
                'INSERT INTO notes(user_id,subject_id,chapter_id,title,body) SELECT %s,id,NULL,%s,%s FROM subjects WHERE id=%s AND user_id=%s RETURNING id',
                (user_id,title,transcript,subject_id,user_id)
            ).fetchone()
        else:
            note = conn.execute(
                'INSERT INTO notes(user_id,subject_id,chapter_id,title,body) SELECT %s,s.id,c.id,%s,%s FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.id=%s AND c.user_id=%s AND s.user_id=%s AND s.id=%s RETURNING id',
                (user_id,title,transcript,chapter_id,user_id,user_id,subject_id)
            ).fetchone()
        if not note:
            return None
        row = conn.execute(
            'INSERT INTO audio_notes(user_id,note_id,subject_id,chapter_id,mime_type,data) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id',
            (user_id, note['id'], subject_id, chapter_id, mime_type, data)
        ).fetchone()
        return row['id'] if row else None

def get_audio_note(user_id, audio_id):
    with connect() as conn:
        return conn.execute('SELECT * FROM audio_notes WHERE id=%s AND user_id=%s', (audio_id,user_id)).fetchone()

def make_snippet(text, words, width=110):
    flat=' '.join((text or '').split())
    if isinstance(words, str):
        words=[words]
    words=[w for w in words if w]
    if not flat or not words:
        return {'before':flat[:width*2],'match':'','after':''}
    lower=flat.lower()
    positions=[lower.find(w.lower()) for w in words if lower.find(w.lower()) >= 0]
    pos=min(positions) if positions else -1
    if pos == -1:
        return {'before':flat[:width*2],'match':'','after':''}
    matched=next((w for w in words if lower.find(w.lower()) == pos), words[0])
    start=max(0,pos-width); end=min(len(flat),pos+len(matched)+width)
    return {'before':('…' if start else '')+flat[start:pos], 'match':flat[pos:pos+len(matched)], 'after':flat[pos+len(matched):end]+('…' if end<len(flat) else '')}


def search(user_id, query, limit=50):
    import re
    raw=(query or '').strip()
    if not raw:
        return {'notes':[],'pages':[],'subjects':[],'chapters':[]}
    # Search by useful words rather than requiring every word to exist on the
    # same page. Ranking decides which result is strongest.
    words=[]
    for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9_'-]*", raw.lower()):
        if len(w) > 1 and w not in {'the','and','for','with','from','into','about','this','that','what','where','when','how','are','was','were','has','have','can','you','your','in','of','to','a','an','on','is','it','my'}:
            if w not in words: words.append(w)
    words=words[:10] or [raw.lower()[:80]]
    patterns=[f'%{w.replace("%", "\\%").replace("_", "\\_")}%' for w in words]
    note_where=' OR '.join('(n.title ILIKE %s OR n.body ILIKE %s)' for _ in words)
    note_params=[p for p in patterns for _ in range(2)]
    page_where=' OR '.join('(pg.text ILIKE %s OR p.original_name ILIKE %s)' for _ in words)
    page_params=[p for p in patterns for _ in range(2)]
    subject_where=' OR '.join('s.name ILIKE %s' for _ in words)
    chapter_where=' OR '.join('(c.name ILIKE %s OR s.name ILIKE %s)' for _ in words)
    chapter_params=[p for p in patterns for _ in range(2)]
    phrase=f'%{raw.replace("%", "\\%").replace("_", "\\_")}%' 
    with connect() as conn:
        subject_rows=conn.execute(
            f'SELECT s.id,s.name FROM subjects s WHERE s.user_id=%s AND ({subject_where}) ORDER BY s.name COLLATE "C" LIMIT %s',
            [user_id,*patterns,limit]).fetchall()
        chapter_rows=conn.execute(
            f'SELECT c.id,c.name,c.subject_id,s.name AS subject_name FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.user_id=%s AND s.user_id=%s AND ({chapter_where}) ORDER BY s.name COLLATE "C",c.name COLLATE "C" LIMIT %s',
            [user_id,user_id,*chapter_params,limit]).fetchall()
        notes_rows=conn.execute(
            f"""SELECT n.id,n.title,n.body,n.subject_id,n.chapter_id,c.name AS chapter_name,s.name AS subject_name
                FROM notes n JOIN subjects s ON s.id=n.subject_id LEFT JOIN chapters c ON c.id=n.chapter_id
                WHERE n.user_id=%s AND s.user_id=%s AND ({note_where})
                ORDER BY CASE WHEN n.title ILIKE %s THEN 0 ELSE 1 END, n.id DESC LIMIT %s""",
            [user_id,user_id,*note_params,phrase,limit]).fetchall()
        page_rows=conn.execute(
            f"""SELECT pg.page_number,pg.text,p.id AS pdf_id,p.original_name,p.page_count,p.file_type,p.subject_id,p.chapter_id,c.name AS chapter_name,s.name AS subject_name
                FROM pdf_pages pg JOIN pdf_files p ON p.id=pg.pdf_id JOIN subjects s ON s.id=p.subject_id LEFT JOIN chapters c ON c.id=p.chapter_id
                WHERE pg.user_id=%s AND p.user_id=%s AND s.user_id=%s AND ({page_where})""",
            [user_id,user_id,user_id,*page_params]).fetchall()
    def score_text(text):
        low=(text or '').lower()
        score=sum(low.count(w) for w in words)
        if raw.lower() in low: score += 8
        return score
    page_results=[]
    for r in page_rows:
        text=r['text'] or ''
        score=score_text(text) + (score_text(r['original_name']) * 2)
        page_results.append(dict(r,score=score,snippet=make_snippet(text,words)))
    page_results.sort(key=lambda x:(-x['score'], int(x['page_number'])))
    note_results=[]
    for r in notes_rows:
        text=f"{r['title']} {r['body']}"
        note_results.append(dict(r,score=score_text(text),snippet=make_snippet(r['body'],words)))
    note_results.sort(key=lambda x:(-x['score'], -x['id']))
    return {'subjects':subject_rows,'chapters':chapter_rows,'notes':note_results[:limit],'pages':page_results[:limit]}

def search_locations(user_id, query='', limit=30):
    q=(query or '').strip()
    pattern=f'%{q}%' if q else '%'
    with connect() as conn:
        subjects=conn.execute(
            'SELECT id,name FROM subjects WHERE user_id=%s AND name ILIKE %s ORDER BY name COLLATE "C" LIMIT %s',
            (user_id,pattern,limit)
        ).fetchall()
        chapters=conn.execute(
            '''SELECT c.id,c.name,c.subject_id,s.name AS subject_name
               FROM chapters c JOIN subjects s ON s.id=c.subject_id
               WHERE c.user_id=%s AND s.user_id=%s
                 AND (c.name ILIKE %s OR s.name ILIKE %s)
               ORDER BY s.name COLLATE "C",c.name COLLATE "C" LIMIT %s''',
            (user_id,user_id,pattern,pattern,limit)
        ).fetchall()
    return {'subjects':subjects,'chapters':chapters}


# ---------- Step 3: study workspace sticky notes ----------
def list_sticky_notes(user_id, pdf_id):
    with connect() as conn:
        return conn.execute("""SELECT id, user_id, subject_id, chapter_id, pdf_id, title, body, color, created_at, updated_at
            FROM sticky_notes WHERE user_id=%s AND pdf_id=%s ORDER BY updated_at DESC, id DESC""", (user_id, pdf_id)).fetchall()

def add_sticky_note(user_id, subject_id, chapter_id, pdf_id, title, body, color='violet'):
    with connect() as conn:
        return conn.execute("""INSERT INTO sticky_notes(user_id,subject_id,chapter_id,pdf_id,title,body,color)
            VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (user_id, subject_id, chapter_id, pdf_id, title, body, color)).fetchone()

def get_sticky_note(user_id, sticky_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM sticky_notes WHERE id=%s AND user_id=%s", (sticky_id,user_id)).fetchone()

def update_sticky_note(user_id, sticky_id, title, body, color):
    with connect() as conn:
        return conn.execute("""UPDATE sticky_notes SET title=%s, body=%s, color=%s, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND user_id=%s RETURNING id""", (title,body,color,sticky_id,user_id)).fetchone()

def delete_sticky_note(user_id, sticky_id):
    with connect() as conn:
        return conn.execute("DELETE FROM sticky_notes WHERE id=%s AND user_id=%s RETURNING id", (sticky_id,user_id)).fetchone()


# ---------- Quiz, progress and revision flashcards (additive tables) ----------
def save_study_quiz(user_id, subject_id, chapter_id, questions, difficulty):
    import json
    with connect() as conn:
        row = conn.execute("""INSERT INTO study_quizzes(user_id,subject_id,chapter_id,questions,difficulty)
            VALUES (%s,%s,%s,%s::jsonb,%s) RETURNING id""",
            (user_id, subject_id, chapter_id, json.dumps(questions), difficulty)).fetchone()
        return row['id']

def get_study_quiz(user_id, quiz_id):
    with connect() as conn:
        return conn.execute('SELECT * FROM study_quizzes WHERE id=%s AND user_id=%s', (quiz_id,user_id)).fetchone()

def save_quiz_attempt(user_id, quiz, correct_answers):
    total = len(quiz['questions'])
    percentage = round((correct_answers / total) * 100, 2) if total else 0
    with connect() as conn:
        row = conn.execute("""INSERT INTO quiz_attempts(quiz_id,user_id,subject_id,chapter_id,total_questions,correct_answers,percentage)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (quiz['id'],user_id,quiz['subject_id'],quiz['chapter_id'],total,correct_answers,percentage)).fetchone()
        return row['id'], total, percentage

def list_quiz_attempts(user_id, limit=10):
    with connect() as conn:
        return conn.execute("""SELECT a.id,a.total_questions,a.correct_answers,a.percentage,a.created_at,
            s.name AS subject_name,c.name AS chapter_name
            FROM quiz_attempts a JOIN subjects s ON s.id=a.subject_id LEFT JOIN chapters c ON c.id=a.chapter_id
            WHERE a.user_id=%s ORDER BY a.created_at DESC LIMIT %s""", (user_id,limit)).fetchall()

def quiz_progress(user_id):
    with connect() as conn:
        summary=conn.execute("""SELECT COUNT(*)::int AS attempts,COALESCE(AVG(percentage),0)::float AS average,
            COALESCE(MAX(percentage),0)::float AS best FROM quiz_attempts WHERE user_id=%s""",(user_id,)).fetchone()
        by_subject=conn.execute('''SELECT s.name AS subject_name,COUNT(a.id)::int AS attempts,
            COALESCE(AVG(a.percentage),0)::float AS average
            FROM quiz_attempts a JOIN subjects s ON s.id=a.subject_id WHERE a.user_id=%s
            GROUP BY s.id,s.name ORDER BY s.name COLLATE "C"''',(user_id,)).fetchall()
    return summary,by_subject

def save_flashcard_deck(user_id, subject_id, chapter_id, cards):
    import json
    with connect() as conn:
        row=conn.execute("""INSERT INTO revision_flashcards(user_id,subject_id,chapter_id,cards)
            VALUES (%s,%s,%s,%s::jsonb) RETURNING id""",(user_id,subject_id,chapter_id,json.dumps(cards))).fetchone()
        return row['id']


def list_recent_study_quiz_questions(user_id, subject_id, chapter_id, limit=10):
    """Return recent generated question text for this exact subject/chapter, to reduce repeats."""
    with connect() as conn:
        if chapter_id is None:
            rows = conn.execute("""SELECT questions FROM study_quizzes
                WHERE user_id=%s AND subject_id=%s AND chapter_id IS NULL
                ORDER BY created_at DESC LIMIT %s""", (user_id, subject_id, limit)).fetchall()
        else:
            rows = conn.execute("""SELECT questions FROM study_quizzes
                WHERE user_id=%s AND subject_id=%s AND chapter_id=%s
                ORDER BY created_at DESC LIMIT %s""", (user_id, subject_id, chapter_id, limit)).fetchall()
    seen = []
    for row in rows:
        questions = row.get('questions') or []
        for question in questions:
            if isinstance(question, dict) and isinstance(question.get('question'), str):
                seen.append(question['question'][:500])
    return seen[:100]


def list_chapter_saved_quizzes(user_id, chapter_id, limit=10):
    """Saved generated quizzes for one chapter, newest first."""
    with connect() as conn:
        return conn.execute("""SELECT q.id,q.difficulty,q.created_at,
            jsonb_array_length(q.questions)::int AS question_count,
            COUNT(a.id)::int AS attempts,COALESCE(MAX(a.percentage),0)::float AS best_score
            FROM study_quizzes q LEFT JOIN quiz_attempts a ON a.quiz_id=q.id AND a.user_id=q.user_id
            WHERE q.user_id=%s AND q.chapter_id=%s
            GROUP BY q.id ORDER BY q.created_at DESC LIMIT %s""", (user_id, chapter_id, limit)).fetchall()


def list_chapter_quiz_attempts(user_id, chapter_id, limit=10):
    with connect() as conn:
        return conn.execute("""SELECT a.id,a.quiz_id,a.total_questions,a.correct_answers,a.percentage,a.created_at,
            q.difficulty,s.name AS subject_name,c.name AS chapter_name
            FROM quiz_attempts a JOIN study_quizzes q ON q.id=a.quiz_id
            JOIN subjects s ON s.id=a.subject_id LEFT JOIN chapters c ON c.id=a.chapter_id
            WHERE a.user_id=%s AND a.chapter_id=%s ORDER BY a.created_at DESC LIMIT %s""",
            (user_id, chapter_id, limit)).fetchall()


def list_flashcard_decks(user_id, chapter_id=None, limit=10):
    with connect() as conn:
        if chapter_id is None:
            return conn.execute("""SELECT f.id,f.cards,f.created_at,s.name AS subject_name,c.name AS chapter_name,
                jsonb_array_length(f.cards)::int AS card_count
                FROM revision_flashcards f JOIN subjects s ON s.id=f.subject_id LEFT JOIN chapters c ON c.id=f.chapter_id
                WHERE f.user_id=%s ORDER BY f.created_at DESC LIMIT %s""", (user_id, limit)).fetchall()
        return conn.execute("""SELECT f.id,f.cards,f.created_at,s.name AS subject_name,c.name AS chapter_name,
            jsonb_array_length(f.cards)::int AS card_count
            FROM revision_flashcards f JOIN subjects s ON s.id=f.subject_id LEFT JOIN chapters c ON c.id=f.chapter_id
            WHERE f.user_id=%s AND f.chapter_id=%s ORDER BY f.created_at DESC LIMIT %s""", (user_id, chapter_id, limit)).fetchall()


def get_flashcard_deck(user_id, deck_id):
    with connect() as conn:
        return conn.execute("""SELECT f.id,f.cards,f.created_at,s.name AS subject_name,c.name AS chapter_name,
            jsonb_array_length(f.cards)::int AS card_count
            FROM revision_flashcards f JOIN subjects s ON s.id=f.subject_id LEFT JOIN chapters c ON c.id=f.chapter_id
            WHERE f.user_id=%s AND f.id=%s""", (user_id, deck_id)).fetchone()

def latest_flashcard_deck(user_id):
    with connect() as conn:
        return conn.execute("""SELECT f.id,f.cards,f.created_at,s.name AS subject_name,c.name AS chapter_name
            FROM revision_flashcards f JOIN subjects s ON s.id=f.subject_id LEFT JOIN chapters c ON c.id=f.chapter_id
            WHERE f.user_id=%s ORDER BY f.created_at DESC LIMIT 1""",(user_id,)).fetchone()


# ---------- Adaptive study planner (additive) ----------
def list_study_plan_tasks(user_id, limit=100):
    with connect() as conn:
        return conn.execute("""SELECT t.*,s.name AS subject_name,c.name AS chapter_name
            FROM study_plan_tasks t JOIN subjects s ON s.id=t.subject_id
            LEFT JOIN chapters c ON c.id=t.chapter_id
            WHERE t.user_id=%s ORDER BY t.completed ASC,t.due_date ASC,t.created_at DESC LIMIT %s""",
            (user_id,limit)).fetchall()

def add_study_plan_task(user_id, subject_id, chapter_id, title, task_type, due_date, duration_minutes, priority='normal', rationale=''):
    with connect() as conn:
        row=conn.execute("""INSERT INTO study_plan_tasks(user_id,subject_id,chapter_id,title,task_type,due_date,duration_minutes,priority,rationale)
            SELECT %s,s.id,%s,%s,%s,%s,%s,%s,%s FROM subjects s WHERE s.id=%s AND s.user_id=%s
            AND (%s IS NULL OR EXISTS(SELECT 1 FROM chapters c WHERE c.id=%s AND c.subject_id=s.id AND c.user_id=%s))
            RETURNING id""",(user_id,chapter_id,title,task_type,due_date,duration_minutes,priority,rationale,subject_id,user_id,chapter_id,chapter_id,user_id)).fetchone()
        return row['id'] if row else None

def set_study_plan_task_completed(user_id, task_id, completed):
    with connect() as conn:
        return conn.execute("""UPDATE study_plan_tasks SET completed=%s,
            completed_at=CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE id=%s AND user_id=%s RETURNING id""",(completed,completed,task_id,user_id)).fetchone()

def reschedule_study_plan_task(user_id, task_id, due_date):
    with connect() as conn:
        return conn.execute("UPDATE study_plan_tasks SET due_date=%s WHERE id=%s AND user_id=%s AND completed=FALSE RETURNING id",(due_date,task_id,user_id)).fetchone()

def update_study_plan_task(user_id, task_id, title, duration_minutes, task_type):
    allowed={'revise','quiz','flashcards','read','practice','audio'}
    if task_type not in allowed: task_type='revise'
    with connect() as conn:
        return conn.execute("""UPDATE study_plan_tasks SET title=%s,duration_minutes=%s,task_type=%s
            WHERE id=%s AND user_id=%s AND completed=FALSE RETURNING id""",
            (title[:160],max(5,min(int(duration_minutes),720)),task_type,task_id,user_id)).fetchone()

def delete_study_plan_task(user_id, task_id):
    with connect() as conn:
        return conn.execute("DELETE FROM study_plan_tasks WHERE id=%s AND user_id=%s RETURNING id",(task_id,user_id)).fetchone()

def planner_chapter_candidates(user_id):
    with connect() as conn:
        return conn.execute('''SELECT c.id AS chapter_id,c.name AS chapter_name,c.subject_id,s.name AS subject_name,
            COUNT(a.id)::int AS attempts,COALESCE(AVG(a.percentage),0)::float AS average,
            MAX(a.created_at) AS last_attempt
            FROM chapters c JOIN subjects s ON s.id=c.subject_id AND s.user_id=c.user_id
            LEFT JOIN quiz_attempts a ON a.chapter_id=c.id AND a.user_id=c.user_id
            WHERE c.user_id=%s GROUP BY c.id,c.name,c.subject_id,s.name
            ORDER BY CASE WHEN COUNT(a.id)=0 THEN 0 ELSE 1 END,
            CASE WHEN COUNT(a.id)=0 THEN 0 ELSE AVG(a.percentage) END ASC,c.name COLLATE "C"''',(user_id,)).fetchall()

# ---------- Custom multi-subject study programs ----------
def list_study_plan_programs(user_id):
    with connect() as conn:
        return conn.execute("""SELECT p.*,COUNT(DISTINCT pc.chapter_id)::int AS chapter_count,
            COUNT(DISTINCT t.id) FILTER (WHERE t.completed)::int AS completed_sessions,
            COUNT(DISTINCT t.id)::int AS total_sessions
            FROM study_plan_programs p
            LEFT JOIN study_plan_program_chapters pc ON pc.program_id=p.id
            LEFT JOIN study_plan_tasks t ON t.program_id=p.id AND t.user_id=p.user_id
            WHERE p.user_id=%s GROUP BY p.id ORDER BY p.updated_at DESC,p.id DESC""",(user_id,)).fetchall()

def save_custom_study_program(user_id, program_id, title, start_date, days, daily_minutes, selections):
    """Create/update a multi-subject program and rebalance pending sessions safely.

    Completed sessions remain as history. Pending sessions are rebuilt only after
    validating that the chosen window can accommodate every selected chapter.
    Returns (program_id, created_count), or (None, 0) if the plan cannot fit.
    """
    days=max(1,min(int(days),30)); daily_minutes=max(30,min(int(daily_minutes),720))
    title=(title or 'My study plan').strip()[:100] or 'My study plan'
    clean=[]; seen=set()
    for subject_id,chapter_id in selections:
        key=(int(subject_id),int(chapter_id))
        if key not in seen:
            seen.add(key); clean.append(key)
    if not clean:
        return None,0

    with connect() as conn:
        program=conn.execute("SELECT id FROM study_plan_programs WHERE id=%s AND user_id=%s",(program_id,user_id)).fetchone() if program_id else None
        pid=program['id'] if program else None
        chapter_rows=[]
        for subject_id,chapter_id in clean:
            valid=conn.execute("""SELECT s.id AS subject_id,c.id AS chapter_id,s.name AS subject_name,c.name AS chapter_name
                FROM subjects s JOIN chapters c ON c.subject_id=s.id AND c.user_id=s.user_id
                WHERE s.id=%s AND s.user_id=%s AND c.id=%s""",(subject_id,user_id,chapter_id)).fetchone()
            if valid: chapter_rows.append(valid)
        if not chapter_rows:
            return None,0

        # Preserve completed work and account for its minutes on its actual date.
        completed_by_date={}; completed_chapters_by_date={}; completed_coverage=set()
        if pid:
            completed=conn.execute("""SELECT due_date,chapter_id,duration_minutes FROM study_plan_tasks
                WHERE program_id=%s AND user_id=%s AND completed=TRUE""",(pid,user_id)).fetchall()
            plan_end=start_date+timedelta(days=days-1)
            selected_ids_for_coverage={c['chapter_id'] for c in chapter_rows}
            for row in completed:
                completed_by_date[row['due_date']]=completed_by_date.get(row['due_date'],0)+int(row['duration_minutes'] or 0)
                completed_chapters_by_date.setdefault(row['due_date'],set()).add(row['chapter_id'])
                if start_date <= row['due_date'] <= plan_end and row['chapter_id'] in selected_ids_for_coverage:
                    completed_coverage.add(row['chapter_id'])

        # Assign useful focus blocks, while respecting the daily time budget.
        day_slots=[]
        for day_index in range(1,days+1):
            due=start_date+timedelta(days=day_index-1)
            remaining=max(0,daily_minutes-completed_by_date.get(due,0))
            already=completed_chapters_by_date.get(due,set())
            eligible=max(0,len(chapter_rows)-len(already.intersection({c['chapter_id'] for c in chapter_rows})))
            if remaining >= 25:
                slots=min(eligible,remaining//25)
            elif remaining >= 10 and eligible:
                slots=1
            else:
                slots=0
            day_slots.append((day_index,due,remaining,slots,already))
        if len(completed_coverage)+sum(item[3] for item in day_slots) < len(chapter_rows):
            return None,0

        # All validation is complete. Now apply changes in this transaction.
        if not pid:
            row=conn.execute("""INSERT INTO study_plan_programs(user_id,title,start_date,days,daily_minutes)
                VALUES(%s,%s,%s,%s,%s) RETURNING id""",(user_id,title,start_date,days,daily_minutes)).fetchone()
            pid=row['id']
        else:
            conn.execute("""UPDATE study_plan_programs SET title=%s,start_date=%s,days=%s,daily_minutes=%s,updated_at=CURRENT_TIMESTAMP
                WHERE id=%s AND user_id=%s""",(title,start_date,days,daily_minutes,pid,user_id))

        # The selected chapter list is the active plan. Historical completed tasks remain.
        conn.execute("DELETE FROM study_plan_program_chapters WHERE program_id=%s AND user_id=%s",(pid,user_id))
        for chapter in chapter_rows:
            conn.execute("""INSERT INTO study_plan_program_chapters(program_id,user_id,subject_id,chapter_id)
                VALUES(%s,%s,%s,%s) ON CONFLICT(program_id,chapter_id) DO NOTHING""",
                (pid,user_id,chapter['subject_id'],chapter['chapter_id']))
        conn.execute("DELETE FROM study_plan_tasks WHERE program_id=%s AND user_id=%s AND completed=FALSE",(pid,user_id))

        created=0; cursor=0
        selected_ids={c['chapter_id'] for c in chapter_rows}
        for day_index,due,remaining,slots,already in day_slots:
            if slots <= 0: continue
            # Avoid scheduling the same chapter twice on a single date.
            day_chapters=set(already).intersection(selected_ids)
            candidates=[c for c in chapter_rows if c['chapter_id'] not in day_chapters]
            if not candidates: continue
            duration_base=remaining//slots; extra=remaining%slots
            for slot in range(slots):
                # Rotate fairly through chapters; skip any already completed that day.
                chapter=None
                for _ in range(len(chapter_rows)):
                    candidate=chapter_rows[cursor % len(chapter_rows)]; cursor+=1
                    if candidate['chapter_id'] not in day_chapters:
                        chapter=candidate; break
                if chapter is None: break
                day_chapters.add(chapter['chapter_id'])
                duration=duration_base+(1 if slot < extra else 0)
                result=conn.execute("""SELECT COUNT(*)::int AS attempts,COALESCE(AVG(percentage),0)::float AS average
                    FROM quiz_attempts WHERE user_id=%s AND chapter_id=%s""",(user_id,chapter['chapter_id'])).fetchone()
                attempts=result['attempts']; average=result['average']
                if attempts == 0:
                    task_type='read'; priority='high'; action='Read notes and understand the key concepts'
                elif average < 60:
                    task_type='practice'; priority='high'; action='Practice weak areas and check explanations'
                elif average < 80:
                    task_type='quiz'; priority='normal'; action='Attempt a short practice quiz'
                else:
                    task_type='flashcards'; priority='low'; action='Revise with flashcards'
                title_text=f"{chapter['chapter_name']} · Day {day_index}"
                rationale=f"Suggested activity: {action}. Planned for day {day_index}; daily budget is {daily_minutes} minutes."
                conn.execute("""INSERT INTO study_plan_tasks(user_id,subject_id,chapter_id,title,task_type,due_date,duration_minutes,
                    priority,rationale,program_id,schedule_day)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (user_id,chapter['subject_id'],chapter['chapter_id'],title_text,task_type,due,duration,priority,rationale,pid,day_index))
                created+=1
        return pid,created

def get_study_plan_program(user_id, program_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM study_plan_programs WHERE id=%s AND user_id=%s",(program_id,user_id)).fetchone()

def list_program_chapters(user_id, program_id):
    with connect() as conn:
        return conn.execute("""SELECT pc.subject_id,pc.chapter_id,s.name AS subject_name,c.name AS chapter_name
            FROM study_plan_program_chapters pc JOIN subjects s ON s.id=pc.subject_id
            JOIN chapters c ON c.id=pc.chapter_id WHERE pc.user_id=%s AND pc.program_id=%s ORDER BY pc.added_at,pc.id""",
            (user_id,program_id)).fetchall()


# ---------- Shared study timer and study-session history ----------
def get_active_study_session(user_id):
    with connect() as conn:
        return conn.execute("""SELECT ss.*,s.name AS subject_name,c.name AS chapter_name
            FROM study_sessions ss JOIN subjects s ON s.id=ss.subject_id
            LEFT JOIN chapters c ON c.id=ss.chapter_id
            WHERE ss.user_id=%s AND ss.status IN ('running','paused') ORDER BY ss.id DESC LIMIT 1""",(user_id,)).fetchone()

def start_study_session(user_id, subject_id, chapter_id=None, title='Study session', planned_minutes=25, task_id=None):
    with connect() as conn:
        active=conn.execute("SELECT id FROM study_sessions WHERE user_id=%s AND status IN ('running','paused') LIMIT 1",(user_id,)).fetchone()
        if active: return None, 'active_exists'
        subject=conn.execute("SELECT id FROM subjects WHERE id=%s AND user_id=%s",(subject_id,user_id)).fetchone()
        if not subject: return None, 'invalid_subject'
        if chapter_id is not None:
            chapter=conn.execute("SELECT id FROM chapters WHERE id=%s AND subject_id=%s AND user_id=%s",(chapter_id,subject_id,user_id)).fetchone()
            if not chapter: chapter_id=None
        if task_id is not None:
            task=conn.execute("SELECT id,subject_id,chapter_id,title,duration_minutes FROM study_plan_tasks WHERE id=%s AND user_id=%s",(task_id,user_id)).fetchone()
            if not task: return None, 'invalid_task'
            subject_id=task['subject_id']; chapter_id=task['chapter_id']; title=task['title']; planned_minutes=task['duration_minutes']
        row=conn.execute("""INSERT INTO study_sessions(user_id,task_id,subject_id,chapter_id,title,planned_minutes,status,running_since)
            VALUES(%s,%s,%s,%s,%s,%s,'running',CURRENT_TIMESTAMP) RETURNING id""",
            (user_id,task_id,subject_id,chapter_id,(title or 'Study session')[:160],max(1,min(int(planned_minutes),720)))).fetchone()
        return row['id'], 'started'

def pause_study_session(user_id, session_id):
    with connect() as conn:
        row=conn.execute("SELECT id,elapsed_seconds,running_since,status FROM study_sessions WHERE id=%s AND user_id=%s AND status='running'",(session_id,user_id)).fetchone()
        if not row: return False
        conn.execute("UPDATE study_sessions SET elapsed_seconds=elapsed_seconds+GREATEST(0,FLOOR(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-running_since)))::int),running_since=NULL,status='paused' WHERE id=%s AND user_id=%s",(session_id,user_id))
        return True

def resume_study_session(user_id, session_id):
    with connect() as conn:
        cur=conn.execute("UPDATE study_sessions SET running_since=CURRENT_TIMESTAMP,status='running' WHERE id=%s AND user_id=%s AND status='paused' RETURNING id",(session_id,user_id)).fetchone()
        return bool(cur)

def finish_study_session(user_id, session_id):
    with connect() as conn:
        row=conn.execute("SELECT id,task_id,elapsed_seconds,running_since,status FROM study_sessions WHERE id=%s AND user_id=%s AND status IN ('running','paused') FOR UPDATE",(session_id,user_id)).fetchone()
        if not row: return None
        extra="GREATEST(0,FLOOR(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-running_since)))::int)" if row['status']=='running' else '0'
        done=conn.execute(f"UPDATE study_sessions SET elapsed_seconds=elapsed_seconds+{extra},running_since=NULL,status='completed',ended_at=CURRENT_TIMESTAMP WHERE id=%s AND user_id=%s RETURNING id,task_id,elapsed_seconds,subject_id,chapter_id,title,planned_minutes,started_at,ended_at",(session_id,user_id)).fetchone()
        # Completing the timer records time only; task completion remains an explicit user action.
        return done

def list_study_sessions(user_id, limit=20):
    with connect() as conn:
        return conn.execute("""SELECT ss.*,s.name AS subject_name,c.name AS chapter_name
            FROM study_sessions ss JOIN subjects s ON s.id=ss.subject_id LEFT JOIN chapters c ON c.id=ss.chapter_id
            WHERE ss.user_id=%s AND ss.status='completed' ORDER BY ss.ended_at DESC NULLS LAST,ss.id DESC LIMIT %s""",(user_id,max(1,min(int(limit),100)))).fetchall()

def study_time_totals(user_id):
    with connect() as conn:
        return conn.execute("""SELECT COALESCE(SUM(elapsed_seconds),0)::bigint AS total_seconds,
            COUNT(*)::int AS completed_sessions,COALESCE(SUM(elapsed_seconds) FILTER (WHERE ended_at::date=CURRENT_DATE),0)::bigint AS today_seconds
            FROM study_sessions WHERE user_id=%s AND status='completed'""",(user_id,)).fetchone()

def get_planner_preferences(user_id):
    with connect() as conn:
        row=conn.execute("SELECT * FROM study_planner_preferences WHERE user_id=%s",(user_id,)).fetchone()
        if row: return row
        return {'user_id':user_id,'daily_target_minutes':180,'preferred_session_minutes':50,'break_minutes':10,'break_enabled':True,'availability_start':None,'availability_end':None}

def save_planner_preferences(user_id, daily_target_minutes, preferred_session_minutes, break_minutes, break_enabled, availability_start=None, availability_end=None):
    daily_target_minutes=max(15,min(int(daily_target_minutes),720)); preferred_session_minutes=max(10,min(int(preferred_session_minutes),180)); break_minutes=max(0,min(int(break_minutes),30))
    with connect() as conn:
        return conn.execute("""INSERT INTO study_planner_preferences(user_id,daily_target_minutes,preferred_session_minutes,break_minutes,break_enabled,availability_start,availability_end,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET
            daily_target_minutes=EXCLUDED.daily_target_minutes,preferred_session_minutes=EXCLUDED.preferred_session_minutes,break_minutes=EXCLUDED.break_minutes,
            break_enabled=EXCLUDED.break_enabled,availability_start=EXCLUDED.availability_start,availability_end=EXCLUDED.availability_end,updated_at=CURRENT_TIMESTAMP RETURNING *""",
            (user_id,daily_target_minutes,preferred_session_minutes,break_minutes,bool(break_enabled),availability_start or None,availability_end or None)).fetchone()


def schedule_study_plan_times(user_id, program_id=None):
    """Assign clock times to pending tasks when the user has configured an availability window.
    Tasks that do not fit are left unscheduled rather than silently overbooking the day.
    """
    prefs=get_planner_preferences(user_id)
    start=prefs.get('availability_start'); end=prefs.get('availability_end')
    if not start or not end: return 0
    start_minutes=start.hour*60+start.minute; end_minutes=end.hour*60+end.minute
    if end_minutes <= start_minutes: return 0
    break_len=int(prefs.get('break_minutes') or 0) if prefs.get('break_enabled') else 0
    with connect() as conn:
        if program_id:
            rows=conn.execute("SELECT id,due_date,duration_minutes FROM study_plan_tasks WHERE user_id=%s AND program_id=%s AND completed=FALSE ORDER BY due_date,id",(user_id,program_id)).fetchall()
        else:
            rows=conn.execute("SELECT id,due_date,duration_minutes FROM study_plan_tasks WHERE user_id=%s AND completed=FALSE ORDER BY due_date,id",(user_id,)).fetchall()
        per_day={}; updated=0
        for row in rows:
            minute=per_day.get(row['due_date'],start_minutes)
            duration=max(1,int(row['duration_minutes'] or 25))
            if minute+duration <= end_minutes:
                clock=datetime.strptime(f"{minute//60:02d}:{minute%60:02d}","%H:%M").time()
                conn.execute("UPDATE study_plan_tasks SET scheduled_time=%s WHERE id=%s AND user_id=%s",(clock,row['id'],user_id))
                per_day[row['due_date']]=minute+duration+break_len; updated+=1
            else:
                conn.execute("UPDATE study_plan_tasks SET scheduled_time=NULL WHERE id=%s AND user_id=%s",(row['id'],user_id))
        return updated


def study_analytics(user_id):
    """Aggregated, completed-session analytics for the current user only."""
    with connect() as conn:
        by_subject = conn.execute("""SELECT s.id AS subject_id,s.name AS subject_name,
            COALESCE(SUM(ss.elapsed_seconds),0)::bigint AS total_seconds,
            COUNT(ss.id)::int AS sessions,
            COALESCE(AVG(ss.elapsed_seconds),0)::int AS average_seconds
            FROM subjects s LEFT JOIN study_sessions ss ON ss.subject_id=s.id AND ss.user_id=%s AND ss.status='completed'
            WHERE s.user_id=%s GROUP BY s.id,s.name ORDER BY total_seconds DESC,s.name""", (user_id,user_id)).fetchall()
        by_chapter = conn.execute("""SELECT c.id AS chapter_id,c.name AS chapter_name,s.name AS subject_name,
            COALESCE(SUM(ss.elapsed_seconds),0)::bigint AS total_seconds,COUNT(ss.id)::int AS sessions
            FROM chapters c JOIN subjects s ON s.id=c.subject_id
            LEFT JOIN study_sessions ss ON ss.chapter_id=c.id AND ss.user_id=%s AND ss.status='completed'
            WHERE c.user_id=%s GROUP BY c.id,c.name,s.name ORDER BY total_seconds DESC,c.name LIMIT 12""", (user_id,user_id)).fetchall()
        daily = conn.execute("""SELECT ended_at::date AS study_date,COALESCE(SUM(elapsed_seconds),0)::bigint AS total_seconds,
            COUNT(*)::int AS sessions FROM study_sessions WHERE user_id=%s AND status='completed'
            AND ended_at >= CURRENT_DATE - INTERVAL '6 days' GROUP BY ended_at::date ORDER BY study_date""", (user_id,)).fetchall()
        quiz = conn.execute("""SELECT COUNT(*)::int AS attempts,COALESCE(AVG(percentage),0)::float AS average_score
            FROM quiz_attempts WHERE user_id=%s""", (user_id,)).fetchone()
        return {'by_subject': by_subject, 'by_chapter': by_chapter, 'daily': daily, 'quiz': quiz}
