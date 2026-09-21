from pathlib import Path
from uuid import uuid4
import os
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from . import database as db
from .pdf_tools import extract_pages

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = Path(os.getenv("STUDY_UPLOAD_DIR", str(BASE_DIR / "uploads")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Study AI", version="1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

@app.on_event("startup")
def startup():
    db.init_db()

@app.get("/health")
def health():
    return {"status": "ok", "app": "Study AI"}

def dashboard_context(request: Request, results=None, query=""):
    subjects, chapters, stats = db.dashboard_data()
    return {"request": request, "subjects": subjects, "chapters": chapters, "stats": stats, "results": results, "query": query}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context=dashboard_context(request))

@app.post("/subjects")
def create_subject(name: str = Form(...)):
    name = name.strip()
    if name:
        db.add_subject(name)
    return RedirectResponse("/", status_code=303)

@app.post("/subjects/{subject_id}/rename")
def rename_subject(subject_id: int, name: str = Form(...)):
    if not db.get_subject(subject_id): raise HTTPException(404)
    if name.strip(): db.rename_subject(subject_id, name.strip())
    return RedirectResponse(f"/subjects/{subject_id}", status_code=303)

@app.post("/subjects/{subject_id}/delete")
def remove_subject(subject_id: int):
    for stored in db.delete_subject(subject_id):
        (UPLOAD_DIR / stored).unlink(missing_ok=True)
    return RedirectResponse("/", status_code=303)

@app.get("/subjects/{subject_id}", response_class=HTMLResponse)
def subject(request: Request, subject_id: int):
    s = db.get_subject(subject_id)
    if not s: raise HTTPException(404)
    return templates.TemplateResponse(request=request, name="subject.html", context={"subject": s, "chapters": db.list_chapters(subject_id)})

@app.post("/subjects/{subject_id}/chapters")
def create_chapter(subject_id: int, name: str = Form(...)):
    if db.get_subject(subject_id) and name.strip(): db.add_chapter(subject_id, name.strip())
    return RedirectResponse(f"/subjects/{subject_id}", status_code=303)

@app.post("/chapters/{chapter_id}/rename")
def rename_chapter(chapter_id: int, name: str = Form(...)):
    c = db.get_chapter(chapter_id)
    if not c: raise HTTPException(404)
    if name.strip(): db.rename_chapter(chapter_id, name.strip())
    return RedirectResponse(f"/chapters/{chapter_id}", status_code=303)

@app.post("/chapters/{chapter_id}/delete")
def remove_chapter(chapter_id: int):
    c = db.get_chapter(chapter_id)
    if not c: raise HTTPException(404)
    for stored in db.delete_chapter(chapter_id): (UPLOAD_DIR / stored).unlink(missing_ok=True)
    return RedirectResponse(f"/subjects/{c['subject_id']}", status_code=303)

@app.get("/chapters/{chapter_id}", response_class=HTMLResponse)
def chapter(request: Request, chapter_id: int):
    c = db.get_chapter(chapter_id)
    if not c: raise HTTPException(404)
    return templates.TemplateResponse(request=request, name="chapter.html", context={"chapter": c, "notes": db.list_notes(chapter_id), "pdfs": db.list_pdfs(chapter_id)})

@app.post("/chapters/{chapter_id}/notes")
def create_note(chapter_id: int, title: str = Form(...), body: str = Form(...)):
    if not db.get_chapter(chapter_id): raise HTTPException(404)
    if title.strip() and body.strip(): db.add_note(chapter_id, title.strip(), body.strip())
    return RedirectResponse(f"/chapters/{chapter_id}", status_code=303)

@app.post("/notes/{note_id}/delete")
def remove_note(note_id: int):
    n = db.get_note(note_id)
    if not n: raise HTTPException(404)
    db.delete_note(note_id)
    return RedirectResponse(f"/chapters/{n['chapter_id']}", status_code=303)

@app.post("/chapters/{chapter_id}/pdfs")
async def upload_pdf(chapter_id: int, file: UploadFile = File(...)):
    if not db.get_chapter(chapter_id): raise HTTPException(404)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are allowed.")
    stored = f"{uuid4().hex}.pdf"
    path = UPLOAD_DIR / stored
    path.write_bytes(await file.read())
    try:
        pages = extract_pages(path)
    except Exception as e:
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read PDF: {e}")
    db.add_pdf(chapter_id, Path(file.filename).name, stored, len(pages), [(i + 1, text) for i, text in enumerate(pages) if text])
    return RedirectResponse(f"/chapters/{chapter_id}", status_code=303)

@app.get("/pdfs/{pdf_id}/open")
def open_pdf(pdf_id: int):
    p = db.get_pdf(pdf_id)
    if not p: raise HTTPException(404)
    path = UPLOAD_DIR / p["stored_name"]
    if not path.exists(): raise HTTPException(404, "PDF file not found.")
    return FileResponse(path, media_type="application/pdf", filename=p["original_name"], content_disposition_type="inline")

@app.get("/pdfs/{pdf_id}/text", response_class=HTMLResponse)
def pdf_text(request: Request, pdf_id: int):
    p = db.get_pdf(pdf_id)
    if not p: raise HTTPException(404)
    return templates.TemplateResponse(request=request, name="pdf_text.html", context={"pdf": p, "pages": db.get_pdf_pages(pdf_id)})

@app.post("/pdfs/{pdf_id}/delete")
def remove_pdf(pdf_id: int):
    p = db.get_pdf(pdf_id)
    if not p: raise HTTPException(404)
    (UPLOAD_DIR / p["stored_name"]).unlink(missing_ok=True)
    db.delete_pdf(pdf_id)
    return RedirectResponse(f"/chapters/{p['chapter_id']}", status_code=303)

@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    results = db.search(q) if q.strip() else None
    return templates.TemplateResponse(request=request, name="index.html", context=dashboard_context(request, results, q))
