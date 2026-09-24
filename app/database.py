import os
from contextlib import contextmanager
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
ALTER TABLE pdf_files ADD COLUMN IF NOT EXISTS file_type VARCHAR(10) NOT NULL DEFAULT 'pdf';
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
        return conn.execute('''SELECT id,title,body,subject_id,chapter_id FROM notes WHERE subject_id=%s AND chapter_id IS NULL AND user_id=%s ORDER BY id DESC''', (subject_id,user_id)).fetchall()

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
        return conn.execute('SELECT id,title,body,chapter_id FROM notes WHERE chapter_id=%s AND user_id=%s ORDER BY id DESC', (chapter_id,user_id)).fetchall()


def get_note(user_id, note_id):
    with connect() as conn:
        return conn.execute('SELECT * FROM notes WHERE id=%s AND user_id=%s', (note_id,user_id)).fetchone()


def add_note(user_id, subject_id, chapter_id, title, body):
    with connect() as conn:
        if chapter_id is None:
            return conn.execute('INSERT INTO notes(user_id,subject_id,chapter_id,title,body) SELECT %s,id,NULL,%s,%s FROM subjects WHERE id=%s AND user_id=%s RETURNING id', (user_id,title,body,subject_id,user_id)).fetchone()
        return conn.execute('INSERT INTO notes(user_id,subject_id,chapter_id,title,body) SELECT %s,s.id,c.id,%s,%s FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.id=%s AND c.user_id=%s AND s.user_id=%s AND s.id=%s RETURNING id', (user_id,title,body,chapter_id,user_id,user_id,subject_id)).fetchone()


def delete_note(user_id, note_id):
    with connect() as conn:
        conn.execute('DELETE FROM notes WHERE id=%s AND user_id=%s', (note_id,user_id))


def add_pdf(user_id, subject_id, chapter_id, original_name, data, page_count, pages, file_type='pdf'):
    with connect() as conn:
        if chapter_id is None:
            row = conn.execute('INSERT INTO pdf_files(user_id,subject_id,chapter_id,original_name,page_count,data,file_type) SELECT %s,id,NULL,%s,%s,%s,%s FROM subjects WHERE id=%s AND user_id=%s RETURNING id', (user_id,original_name,page_count,data,file_type,subject_id,user_id)).fetchone()
        else:
            row = conn.execute('INSERT INTO pdf_files(user_id,subject_id,chapter_id,original_name,page_count,data,file_type) SELECT %s,s.id,c.id,%s,%s,%s,%s FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.id=%s AND c.user_id=%s AND s.user_id=%s AND s.id=%s RETURNING id', (user_id,original_name,page_count,data,file_type,chapter_id,user_id,user_id,subject_id)).fetchone()
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


def get_pdf_pages(user_id, pdf_id):
    with connect() as conn:
        return conn.execute('SELECT page_number,text FROM pdf_pages WHERE pdf_id=%s AND user_id=%s ORDER BY page_number', (pdf_id,user_id)).fetchall()


def delete_pdf(user_id, pdf_id):
    with connect() as conn:
        conn.execute('DELETE FROM pdf_files WHERE id=%s AND user_id=%s', (pdf_id,user_id))


def add_audio_note(user_id, subject_id, chapter_id, title, transcript, data, mime_type='audio/webm'):
    with connect() as conn:
        note = add_note(user_id, subject_id, chapter_id, title, transcript)
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

def make_snippet(text, word, width=70):
    flat=' '.join(text.split()); pos=flat.lower().find(word.lower())
    if pos == -1: return {'before':flat[:width*2],'match':'','after':''}
    start=max(0,pos-width); end=min(len(flat),pos+len(word)+width)
    return {'before':('…' if start else '')+flat[start:pos],'match':flat[pos:pos+len(word)],'after':flat[pos+len(word):end]+('…' if end<len(flat) else '')}


def search(user_id, query, limit=50):
    words=[w.strip() for w in query.split() if w.strip()][:6]
    if not words:
        return {'notes':[],'pages':[],'subjects':[],'chapters':[]}
    patterns=[f'%{w.replace("%", "\\%").replace("_", "\\_")}%' for w in words]
    note_where=' AND '.join('(n.title ILIKE %s OR n.body ILIKE %s)' for _ in words)
    note_params=[p for p in patterns for _ in range(2)]
    page_where=' AND '.join('pg.text ILIKE %s' for _ in words)
    subject_where=' AND '.join('s.name ILIKE %s' for _ in words)
    chapter_where=' AND '.join('(c.name ILIKE %s OR s.name ILIKE %s)' for _ in words)
    chapter_params=[p for p in patterns for _ in range(2)]
    with connect() as conn:
        subject_rows=conn.execute(
            f'SELECT s.id,s.name FROM subjects s WHERE s.user_id=%s AND {subject_where} ORDER BY s.name COLLATE "C" LIMIT %s',
            [user_id,*patterns,limit]).fetchall()
        chapter_rows=conn.execute(
            f'SELECT c.id,c.name,c.subject_id,s.name AS subject_name FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.user_id=%s AND s.user_id=%s AND {chapter_where} ORDER BY s.name COLLATE "C",c.name COLLATE "C" LIMIT %s',
            [user_id,user_id,*chapter_params,limit]).fetchall()
        notes_rows=conn.execute(
            f'SELECT n.id,n.title,n.body,n.subject_id,n.chapter_id,c.name AS chapter_name,s.name AS subject_name FROM notes n JOIN subjects s ON s.id=n.subject_id LEFT JOIN chapters c ON c.id=n.chapter_id WHERE n.user_id=%s AND s.user_id=%s AND {note_where} ORDER BY n.id DESC LIMIT %s',
            [user_id,user_id,*note_params,limit]).fetchall()
        page_rows=conn.execute(
            f'SELECT pg.page_number,pg.text,p.id AS pdf_id,p.original_name,p.file_type,p.subject_id,p.chapter_id,c.name AS chapter_name,s.name AS subject_name FROM pdf_pages pg JOIN pdf_files p ON p.id=pg.pdf_id JOIN subjects s ON s.id=p.subject_id LEFT JOIN chapters c ON c.id=p.chapter_id WHERE pg.user_id=%s AND p.user_id=%s AND s.user_id=%s AND {page_where} ORDER BY p.id DESC,pg.page_number LIMIT %s',
            [user_id,user_id,user_id,*patterns,limit]).fetchall()
    first=words[0]
    return {'subjects':subject_rows,'chapters':chapter_rows,'notes':[dict(r,snippet=make_snippet(r['body'],first)) for r in notes_rows], 'pages':[dict(r,snippet=make_snippet(r['text'],first)) for r in page_rows]}

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
