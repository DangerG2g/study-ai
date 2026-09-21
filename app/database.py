import sqlite3
from contextlib import contextmanager
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("STUDY_DB_PATH", str(BASE_DIR / "study.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pdf_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pdf_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id INTEGER NOT NULL REFERENCES pdf_files(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL
);
"""

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)

def list_subjects():
    with connect() as conn:
        return conn.execute("""
            SELECT s.id, s.name, COUNT(c.id) AS chapter_count
            FROM subjects s LEFT JOIN chapters c ON c.subject_id=s.id
            GROUP BY s.id ORDER BY s.name COLLATE NOCASE
        """).fetchall()

def dashboard_data():
    with connect() as conn:
        subjects = conn.execute("""
            SELECT s.id, s.name, COUNT(c.id) AS chapter_count
            FROM subjects s LEFT JOIN chapters c ON c.subject_id=s.id
            GROUP BY s.id ORDER BY s.name COLLATE NOCASE
        """).fetchall()
        total_chapters = conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        total_notes = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        total_pdfs = conn.execute("SELECT COUNT(*) FROM pdf_files").fetchone()[0]
        chapters = conn.execute("""
            SELECT c.id, c.name, c.subject_id, s.name subject_name
            FROM chapters c JOIN subjects s ON s.id=c.subject_id
            ORDER BY s.name COLLATE NOCASE, c.name COLLATE NOCASE
        """).fetchall()
    return subjects, chapters, {"chapters": total_chapters, "notes": total_notes, "pdfs": total_pdfs}

def get_subject(subject_id):
    with connect() as conn:
        return conn.execute("SELECT id,name FROM subjects WHERE id=?", (subject_id,)).fetchone()

def add_subject(name):
    with connect() as conn:
        return conn.execute("INSERT INTO subjects(name) VALUES(?)", (name,)).lastrowid

def rename_subject(subject_id, name):
    with connect() as conn:
        conn.execute("UPDATE subjects SET name=? WHERE id=?", (name, subject_id))

def delete_subject(subject_id):
    with connect() as conn:
        rows = conn.execute("""
            SELECT p.stored_name FROM pdf_files p
            JOIN chapters c ON c.id=p.chapter_id WHERE c.subject_id=?
        """, (subject_id,)).fetchall()
        conn.execute("DELETE FROM subjects WHERE id=?", (subject_id,))
    return [r["stored_name"] for r in rows]

def list_chapters(subject_id):
    with connect() as conn:
        return conn.execute("""
            SELECT c.id,c.name,
            (SELECT COUNT(*) FROM notes n WHERE n.chapter_id=c.id) note_count,
            (SELECT COUNT(*) FROM pdf_files p WHERE p.chapter_id=c.id) pdf_count
            FROM chapters c WHERE c.subject_id=? ORDER BY c.id
        """, (subject_id,)).fetchall()

def get_chapter(chapter_id):
    with connect() as conn:
        return conn.execute("""
            SELECT c.id,c.name,c.subject_id,s.name subject_name
            FROM chapters c JOIN subjects s ON s.id=c.subject_id
            WHERE c.id=?
        """, (chapter_id,)).fetchone()

def add_chapter(subject_id, name):
    with connect() as conn:
        return conn.execute("INSERT INTO chapters(subject_id,name) VALUES(?,?)", (subject_id,name)).lastrowid

def rename_chapter(chapter_id, name):
    with connect() as conn:
        conn.execute("UPDATE chapters SET name=? WHERE id=?", (name, chapter_id))

def delete_chapter(chapter_id):
    with connect() as conn:
        rows = conn.execute("SELECT stored_name FROM pdf_files WHERE chapter_id=?", (chapter_id,)).fetchall()
        conn.execute("DELETE FROM chapters WHERE id=?", (chapter_id,))
    return [r["stored_name"] for r in rows]

def list_notes(chapter_id):
    with connect() as conn:
        return conn.execute("""
            SELECT id,title,body,created_at FROM notes
            WHERE chapter_id=? ORDER BY id DESC
        """, (chapter_id,)).fetchall()

def get_note(note_id):
    with connect() as conn:
        return conn.execute("SELECT id,chapter_id FROM notes WHERE id=?", (note_id,)).fetchone()

def add_note(chapter_id,title,body):
    with connect() as conn:
        return conn.execute("INSERT INTO notes(chapter_id,title,body) VALUES(?,?,?)", (chapter_id,title,body)).lastrowid

def delete_note(note_id):
    with connect() as conn:
        conn.execute("DELETE FROM notes WHERE id=?", (note_id,))

def list_pdfs(chapter_id):
    with connect() as conn:
        return conn.execute("""
            SELECT p.id,p.original_name,p.page_count,p.created_at,
            (SELECT COUNT(*) FROM pdf_pages g WHERE g.pdf_id=p.id) text_pages
            FROM pdf_files p WHERE p.chapter_id=? ORDER BY p.id DESC
        """, (chapter_id,)).fetchall()

def get_pdf(pdf_id):
    with connect() as conn:
        return conn.execute("SELECT id,chapter_id,original_name,stored_name FROM pdf_files WHERE id=?", (pdf_id,)).fetchone()

def get_pdf_pages(pdf_id):
    with connect() as conn:
        return conn.execute("SELECT page_number, text FROM pdf_pages WHERE pdf_id=? ORDER BY page_number", (pdf_id,)).fetchall()

def add_pdf(chapter_id,original_name,stored_name,page_count,pages):
    with connect() as conn:
        pdf_id = conn.execute("""
            INSERT INTO pdf_files(chapter_id,original_name,stored_name,page_count)
            VALUES(?,?,?,?)
        """, (chapter_id,original_name,stored_name,page_count)).lastrowid
        conn.executemany("INSERT INTO pdf_pages(pdf_id,page_number,text) VALUES(?,?,?)", [(pdf_id,n,t) for n,t in pages])
        return pdf_id

def delete_pdf(pdf_id):
    with connect() as conn:
        conn.execute("DELETE FROM pdf_files WHERE id=?", (pdf_id,))

def _like_pattern(word):
    escaped = word.replace("!","!!").replace("%","!%").replace("_","!_")
    return f"%{escaped}%"

def make_snippet(text,word,width=70):
    flat=" ".join(text.split())
    pos=flat.lower().find(word.lower())
    if pos == -1:
        return {"before":flat[:width*2],"match":"","after":""}
    start=max(0,pos-width); end=min(len(flat),pos+len(word)+width)
    return {"before":("…" if start else "")+flat[start:pos],"match":flat[pos:pos+len(word)],"after":flat[pos+len(word):end]+("…" if end<len(flat) else "")}

def search(query,limit=50):
    words=query.split()[:6]
    if not words: return {"notes":[],"pages":[]}
    patterns=[_like_pattern(w) for w in words]
    note_where=" AND ".join("(n.title LIKE ? ESCAPE '!' OR n.body LIKE ? ESCAPE '!')" for _ in words)
    note_params=[p for p in patterns for _ in range(2)]
    page_where=" AND ".join("pg.text LIKE ? ESCAPE '!'" for _ in words)
    with connect() as conn:
        notes_rows=conn.execute(f"""
            SELECT n.id,n.title,n.body,c.id chapter_id,c.name chapter_name,s.name subject_name
            FROM notes n JOIN chapters c ON c.id=n.chapter_id JOIN subjects s ON s.id=c.subject_id
            WHERE {note_where} ORDER BY n.id DESC LIMIT ?
        """, note_params+[limit]).fetchall()
        page_rows=conn.execute(f"""
            SELECT pg.page_number,pg.text,p.id pdf_id,p.original_name,c.id chapter_id,c.name chapter_name,s.name subject_name
            FROM pdf_pages pg JOIN pdf_files p ON p.id=pg.pdf_id JOIN chapters c ON c.id=p.chapter_id JOIN subjects s ON s.id=c.subject_id
            WHERE {page_where} ORDER BY p.id DESC,pg.page_number LIMIT ?
        """, patterns+[limit]).fetchall()
    first=words[0]
    return {"notes":[dict(r,snippet=make_snippet(r["body"],first)) for r in notes_rows],"pages":[dict(r,snippet=make_snippet(r["text"],first)) for r in page_rows]}
