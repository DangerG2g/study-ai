from pathlib import Path
from datetime import datetime, timedelta, timezone
import base64, hashlib, hmac, os, re, secrets, shutil, subprocess, tempfile

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import database as db
from .document_tools import extract_document

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv('STUDY_SECRET_KEY')
if not SECRET_KEY:
    # Works locally; Render should set a permanent secret in Environment Variables.
    SECRET_KEY = 'local-dev-change-me'
serializer = URLSafeTimedSerializer(SECRET_KEY, salt='study-ai-session-v2')
SESSION_MAX_AGE = 60 * 60 * 24 * 7
MAX_LOGIN_ATTEMPTS = 5
LOCK_MINUTES = 15
PASSWORD_MIN = 8

app = FastAPI(title='Study AI', version='2.0')
app.mount('/static', StaticFiles(directory=BASE_DIR / 'app' / 'static'), name='static')
templates = Jinja2Templates(directory=BASE_DIR / 'app' / 'templates')

def build_office_preview(data: bytes, ext: str, original_name: str):
    """Convert DOC/DOCX/PPT/PPTX to a PDF preview when LibreOffice is available.
    The original Office file remains the user's source file.
    """
    office = shutil.which('libreoffice') or shutil.which('soffice')
    if not office or ext not in {'.doc', '.docx', '.ppt', '.pptx'}:
        return None, 0
    with tempfile.TemporaryDirectory(prefix='study-office-') as td:
        src = Path(td) / (Path(original_name).stem + ext)
        out = Path(td) / 'preview'
        out.mkdir()
        src.write_bytes(data)
        try:
            proc = subprocess.run([office, '--headless', '--convert-to', 'pdf', '--outdir', str(out), str(src)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
        except Exception:
            return None, 0
        pdf = out / (src.stem + '.pdf')
        if proc.returncode != 0 or not pdf.exists():
            return None, 0
        preview = pdf.read_bytes()
        try:
            from pypdf import PdfReader
            count = len(PdfReader(__import__('io').BytesIO(preview)).pages) or 1
        except Exception:
            count = 1
        return preview, count


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return 'scrypt$16384$8$1$' + base64.urlsafe_b64encode(salt).decode() + '$' + base64.urlsafe_b64encode(derived).decode()


def verify_password(password, stored):
    try:
        _, n, r, p, salt_b64, hash_b64 = stored.split('$')
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        derived = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=64)
        return hmac.compare_digest(base64.urlsafe_b64encode(derived).decode(), hash_b64)
    except Exception:
        return False


def make_session(user_id):
    csrf = secrets.token_urlsafe(32)
    return serializer.dumps({'uid': int(user_id), 'csrf': csrf})


def current_user(request: Request):
    raw = request.cookies.get('study_session')
    if not raw: return None
    try:
        data = serializer.loads(raw, max_age=SESSION_MAX_AGE)
        return db.get_user(int(data['uid']))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None


def session_data(request):
    raw = request.cookies.get('study_session')
    if not raw: return None
    try: return serializer.loads(raw, max_age=SESSION_MAX_AGE)
    except Exception: return None


def csrf_for(request):
    data = session_data(request)
    return data.get('csrf') if data else ''


def require_user(request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={'Location':'/login'})
    return user


def require_csrf(request, token):
    data = session_data(request)
    if not data or not token or not hmac.compare_digest(token, data.get('csrf','')):
        raise HTTPException(403, 'Invalid security token. Refresh the page and try again.')


def render(request, name, context=None, status_code=200):
    user = current_user(request)
    ctx = dict(context or {})
    ctx.update({'request': request, 'user': user, 'csrf_token': csrf_for(request)})
    return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)


def auth_redirect(path='/login'):
    return RedirectResponse(path, status_code=303)

@app.on_event('startup')
def startup():
    db.init_db()
    # Demo account for mentor presentations. It is only created if missing.
    if not db.get_user_by_username('demo'):
        db.create_user('demo', hash_password('Demo@12345'))

@app.get('/health')
def health():
    return {'status':'ok','app':'Study AI','version':'2.0'}

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request): return RedirectResponse('/', status_code=303)
    return render(request, 'login.html', {'error':None})

@app.post('/login', response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    u = db.get_user_by_username(username)
    now = datetime.now(timezone.utc)
    if u and u['locked_until'] and u['locked_until'] > now:
        remaining = max(1, int((u['locked_until'] - now).total_seconds() // 60) + 1)
        return render(request, 'login.html', {'error':f'Too many failed attempts. Try again in about {remaining} minutes.'}, 429)
    if not u or not verify_password(password, u['password_hash']):
        if u:
            attempts = u['failed_attempts'] + 1
            locked = now + timedelta(minutes=LOCK_MINUTES) if attempts >= MAX_LOGIN_ATTEMPTS else None
            db.record_failed_login(u['id'], 0 if locked else attempts, locked)
        return render(request, 'login.html', {'error':'Invalid username or password.'}, 401)
    db.reset_login_failures(u['id'])
    response = RedirectResponse('/', status_code=303)
    response.set_cookie('study_session', make_session(u['id']), max_age=SESSION_MAX_AGE, httponly=True, secure=bool(os.getenv('RENDER')), samesite='lax')
    return response

@app.post('/demo-login')
def demo_login(request: Request):
    u = db.get_user_by_username('demo')
    if not u:
        raise HTTPException(503, 'Demo account is not available yet. Refresh once and try again.')
    response = RedirectResponse('/', status_code=303)
    response.set_cookie('study_session', make_session(u['id']), max_age=SESSION_MAX_AGE, httponly=True, secure=bool(os.getenv('RENDER')), samesite='lax')
    return response

@app.get('/change-password', response_class=HTMLResponse)
def change_password_page(request: Request):
    require_user(request)
    return render(request, 'change_password.html', {'error': None, 'success': None})

@app.post('/change-password', response_class=HTMLResponse)
def change_password(request: Request, current_password: str = Form(...), new_password: str = Form(...), confirm_password: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request, csrf_token)
    record=db.get_user_by_username(user['username'])
    if not record or not verify_password(current_password, record['password_hash']):
        return render(request, 'change_password.html', {'error':'Current password is incorrect.', 'success':None}, 400)
    if len(new_password) < PASSWORD_MIN:
        return render(request, 'change_password.html', {'error':f'New password must be at least {PASSWORD_MIN} characters.', 'success':None}, 400)
    if new_password.lower() == user['username'].lower():
        return render(request, 'change_password.html', {'error':'Password cannot be the same as your username.', 'success':None}, 400)
    if new_password != confirm_password:
        return render(request, 'change_password.html', {'error':'New passwords do not match.', 'success':None}, 400)
    db.change_password(user['id'], hash_password(new_password))
    return render(request, 'change_password.html', {'error':None, 'success':'Password changed successfully.'})

@app.get('/forgot-password', response_class=HTMLResponse)
def forgot_password_page(request: Request):
    if current_user(request):
        return RedirectResponse('/change-password', status_code=303)
    return render(request, 'forgot_password.html', {'demo_username':'demo', 'demo_password':'Demo@12345'})

@app.get('/register', response_class=HTMLResponse)
def register_page(request: Request):
    if current_user(request): return RedirectResponse('/', status_code=303)
    return render(request, 'register.html', {'error':None})

@app.post('/register', response_class=HTMLResponse)
def register(request: Request, username: str = Form(...), password: str = Form(...), password_confirm: str = Form(...)):
    u = username.strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{3,30}', u):
        return render(request,'register.html',{'error':'Username must be 3–30 characters and use only letters, numbers, or underscores.'},400)
    if password != password_confirm:
        return render(request,'register.html',{'error':'Passwords do not match.'},400)
    if len(password) < PASSWORD_MIN:
        return render(request,'register.html',{'error':'Password must be at least 8 characters.'},400)
    if u.lower() == password.lower():
        return render(request,'register.html',{'error':'Password cannot be the same as your username.'},400)
    if db.get_user_by_username(u):
        return render(request,'register.html',{'error':'That username is already taken. Choose another username.'},409)
    try:
        user = db.create_user(u, hash_password(password))
    except Exception:
        return render(request,'register.html',{'error':'That username is already taken. Choose another username.'},409)
    response = RedirectResponse('/', status_code=303)
    response.set_cookie('study_session', make_session(user['id']), max_age=SESSION_MAX_AGE, httponly=True, secure=bool(os.getenv('RENDER')), samesite='lax')
    return response

@app.post('/logout')
def logout(request: Request, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie('study_session')
    return response


def dashboard_context(request, user, results=None, query=''):
    subjects, chapters, stats = db.dashboard_data(user['id'])
    return {'subjects':subjects,'chapters':chapters,'stats':stats,'results':results,'query':query}

@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if not user: return auth_redirect()
    return render(request,'index.html',dashboard_context(request,user))

@app.post('/subjects')
def create_subject(request: Request, name: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    if name.strip(): db.add_subject(user['id'],name.strip())
    return RedirectResponse('/',303)

@app.post('/subjects/{subject_id}/rename')
def rename_subject(request: Request, subject_id: int, name: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    if not db.get_subject(user['id'],subject_id): raise HTTPException(404)
    if name.strip(): db.rename_subject(user['id'],subject_id,name.strip())
    return RedirectResponse(f'/subjects/{subject_id}',303)

@app.post('/subjects/{subject_id}/delete')
def remove_subject(request: Request, subject_id: int, csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    if not db.get_subject(user['id'],subject_id): raise HTTPException(404)
    db.delete_subject(user['id'],subject_id)
    return RedirectResponse('/',303)

@app.get('/subjects/{subject_id}', response_class=HTMLResponse)
def subject(request: Request, subject_id: int):
    user=require_user(request); s=db.get_subject(user['id'],subject_id)
    if not s: raise HTTPException(404)
    return render(request,'subject.html',{'subject':s,'chapters':db.list_chapters(user['id'],subject_id),'notes':db.list_subject_notes(user['id'],subject_id),'pdfs':db.list_subject_pdfs(user['id'],subject_id)})

@app.post('/subjects/{subject_id}/chapters')
def create_chapter(request: Request, subject_id: int, name: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    if not db.get_subject(user['id'],subject_id): raise HTTPException(404)
    if name.strip(): db.add_chapter(user['id'],subject_id,name.strip())
    return RedirectResponse(f'/subjects/{subject_id}',303)

@app.post('/chapters/{chapter_id}/rename')
def rename_chapter(request: Request, chapter_id: int, name: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    c=db.get_chapter(user['id'],chapter_id)
    if not c: raise HTTPException(404)
    if name.strip(): db.rename_chapter(user['id'],chapter_id,name.strip())
    return RedirectResponse(f'/chapters/{chapter_id}',303)

@app.post('/chapters/{chapter_id}/delete')
def remove_chapter(request: Request, chapter_id: int, csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    c=db.get_chapter(user['id'],chapter_id)
    if not c: raise HTTPException(404)
    db.delete_chapter(user['id'],chapter_id)
    return RedirectResponse(f'/subjects/{c["subject_id"]}',303)

@app.get('/chapters/{chapter_id}', response_class=HTMLResponse)
def chapter(request: Request, chapter_id: int):
    user=require_user(request); c=db.get_chapter(user['id'],chapter_id)
    if not c: raise HTTPException(404)
    return render(request,'chapter.html',{'chapter':c,'notes':db.list_notes(user['id'],chapter_id),'pdfs':db.list_pdfs(user['id'],chapter_id)})

@app.post('/chapters/{chapter_id}/notes')
def create_note(request: Request, chapter_id: int, title: str = Form(...), body: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    c=db.get_chapter(user['id'],chapter_id)
    if not c: raise HTTPException(404)
    if title.strip() and body.strip(): db.add_note(user['id'],c['subject_id'],chapter_id,title.strip(),body.strip())
    return RedirectResponse(f'/chapters/{chapter_id}',303)

@app.post('/notes/{note_id}/edit')
def edit_note(request: Request, note_id: int, title: str = Form(...), body: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    n=db.get_note(user['id'],note_id)
    if not n: raise HTTPException(404)
    if not title.strip() or not body.strip(): raise HTTPException(400,'Title and note cannot be empty.')
    db.update_note(user['id'],note_id,title.strip(),body.strip())
    return RedirectResponse(f'/chapters/{n["chapter_id"]}' if n['chapter_id'] else f'/subjects/{n["subject_id"]}',303)

@app.post('/notes/{note_id}/delete')
def remove_note(request: Request, note_id: int, csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    n=db.get_note(user['id'],note_id)
    if not n: raise HTTPException(404)
    db.delete_note(user['id'],note_id)
    return RedirectResponse(f'/chapters/{n["chapter_id"]}' if n['chapter_id'] else f'/subjects/{n["subject_id"]}',303)

@app.get('/voice-note')
def voice_note_page(request: Request):
    require_user(request)
    # Legacy route: voice notes now live inside Add Material or the current subject/chapter.
    return RedirectResponse('/add-material?type=voice',303)

@app.get('/add-material', response_class=HTMLResponse)
def add_material_page(request: Request, q: str = '', type: str = 'file', subject_id: int = 0, chapter_id: int = 0, chapter_q: str = ''):
    user=require_user(request)
    locations=db.search_locations(user['id'],q)
    initial_type = type.lower() if type.lower() in {'file','note','voice'} else 'file'
    selected_subject = db.get_subject(user['id'], subject_id) if subject_id else None
    selected_chapter = db.get_chapter(user['id'], chapter_id) if chapter_id else None
    if selected_chapter and (not selected_subject or selected_chapter['subject_id'] != selected_subject['id']):
        selected_chapter = None
    subject_chapters = db.list_chapters(user['id'], subject_id) if selected_subject else []
    if chapter_q.strip():
        cq = chapter_q.strip().lower()
        subject_chapters = [c for c in subject_chapters if cq in c['name'].lower()]
    return render(request,'add_material.html',{'locations':locations,'query':q,'chapter_query':chapter_q,'subject_chapters':subject_chapters,'initial_type':initial_type,'selected_subject':selected_subject,'selected_chapter':selected_chapter})


@app.post('/add-material/create-subject')
def add_material_create_subject(request: Request, name: str = Form(...), type: str = Form('file'), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    if not name.strip(): raise HTTPException(400,'Subject name is required.')
    subject=db.add_subject(user['id'], name.strip())
    return RedirectResponse(f'/add-material?subject_id={subject["id"]}&type={type}',303)

@app.post('/add-material/create-chapter')
def add_material_create_chapter(request: Request, subject_id: int = Form(...), name: str = Form(...), type: str = Form('file'), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    if not db.get_subject(user['id'],subject_id): raise HTTPException(404,'Subject not found.')
    if not name.strip(): raise HTTPException(400,'Chapter name is required.')
    chapter=db.add_chapter(user['id'],subject_id,name.strip())
    return RedirectResponse(f'/add-material?subject_id={subject_id}&chapter_id={chapter["id"]}&type={type}',303)

@app.get('/add-notes')
def add_notes_alias(request: Request):
    require_user(request)
    return RedirectResponse('/add-material?type=note', status_code=303)

@app.post('/materials/note')
def create_material_note(request: Request, subject_id: int = Form(...), chapter_id: str = Form(''), title: str = Form(...), body: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    subject=db.get_subject(user['id'],subject_id)
    if not subject: raise HTTPException(404,'Subject not found.')
    chapter=int(chapter_id) if chapter_id.strip() else None
    if chapter:
        c=db.get_chapter(user['id'],chapter)
        if not c or c['subject_id'] != subject_id: raise HTTPException(400,'Invalid chapter.')
    if not title.strip() or not body.strip(): raise HTTPException(400,'Title and note cannot be empty.')
    db.add_note(user['id'],subject_id,chapter,title.strip(),body.strip())
    return RedirectResponse(f'/chapters/{chapter}' if chapter else f'/subjects/{subject_id}',303)

@app.post('/materials/file')
async def upload_material(request: Request, subject_id: int = Form(...), chapter_id: str = Form(''), file: UploadFile = File(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    subject=db.get_subject(user['id'],subject_id)
    if not subject: raise HTTPException(404,'Subject not found.')
    chapter=int(chapter_id) if chapter_id.strip() else None
    if chapter:
        c=db.get_chapter(user['id'],chapter)
        if not c or c['subject_id'] != subject_id: raise HTTPException(400,'Invalid chapter.')
    name=Path(file.filename or '').name
    ext=Path(name).suffix.lower()
    allowed={'.pdf':'pdf','.doc':'doc','.docx':'docx','.ppt':'ppt','.pptx':'pptx','.jpg':'jpg','.jpeg':'jpeg','.png':'png','.webp':'webp','.gif':'gif'}
    if ext not in allowed: raise HTTPException(400,'Supported files: PDF, DOC/DOCX, PPT/PPTX, JPG, JPEG, PNG, WEBP, and GIF.')
    data=await file.read()
    if len(data)>25*1024*1024: raise HTTPException(413,'File is too large. Maximum size is 25 MB.')
    pages=[]
    extraction_warning=''
    if ext in {'.pdf','.docx','.pptx'}:
        temp=BASE_DIR/'uploads'/f'{secrets.token_hex(12)}{ext}'
        temp.parent.mkdir(exist_ok=True)
        temp.write_bytes(data)
        try:
            pages=extract_document(temp)
        except Exception as e:
            # Never lose a user's original Office/PDF file just because text
            # extraction failed. Store the original and let the reader show
            # a clear fallback/download option.
            pages=[]
            extraction_warning=str(e)[:240]
        finally:
            temp.unlink(missing_ok=True)
    preview_data=None
    preview_page_count=0
    if ext in {'.doc','.docx','.ppt','.pptx'}:
        preview_data, preview_page_count = build_office_preview(data, ext, name)
    actual_page_count=len(pages) if pages else 1
    if ext == '.pdf' and not pages:
        try:
            from pypdf import PdfReader
            actual_page_count=len(PdfReader(__import__('io').BytesIO(data)).pages) or 1
        except Exception:
            actual_page_count=1
    file_id=db.add_pdf(user['id'],subject_id,chapter,name,data,actual_page_count,[(i,t) for i,t in pages if t],allowed[ext],preview_data,preview_page_count)
    if not file_id:
        raise HTTPException(500,'The file could not be saved. Please try again.')
    return RedirectResponse(f'/chapters/{chapter}' if chapter else f'/subjects/{subject_id}',303)

@app.post('/chapters/{chapter_id}/pdfs')
async def upload_document(request: Request, chapter_id: int, file: UploadFile = File(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    c=db.get_chapter(user['id'],chapter_id)
    if not c: raise HTTPException(404)
    # Keep the old endpoint working while using the new optional-chapter storage model.
    return await upload_material(request, subject_id=c['subject_id'], chapter_id=str(chapter_id), file=file, csrf_token=csrf_token)

@app.get('/assistant', response_class=HTMLResponse)
def assistant_page(request: Request, q: str = ''):
    user=require_user(request)
    results = db.search(user['id'], q, limit=12) if q.strip() else None
    return render(request, 'assistant.html', {'query': q, 'results': results, 'answer_parts': [], 'source_count': 0})

@app.post('/assistant/ask', response_class=HTMLResponse)
def assistant_ask(request: Request, question: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    q=question.strip()
    if not q:
        return render(request,'assistant.html',{'query':'','results':None,'answer_parts':[],'source_count':0,'error':'Please enter a question.'},400)
    results=db.search(user['id'],q,limit=12)
    # Ask Study is retrieval-first: pull the strongest matching sentences from
    # the user's own notes/files instead of pretending a generic web answer is
    # coming from an AI model. This gives a useful answer while staying grounded.
    terms=[w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9_'-]*",q) if len(w)>1]
    candidates=[]
    for r in (results or {}).get('notes',[])[:8]:
        text=r['body'] or ''
        for sentence in re.split(r'(?<=[.!?])\s+|\n+', text):
            sentence=sentence.strip()
            if sentence and any(t in sentence.lower() for t in terms):
                score=sum(sentence.lower().count(t) for t in terms)
                candidates.append((score, sentence, f"Note: {r['title']}"))
    for r in (results or {}).get('pages',[])[:8]:
        text=r['text'] or ''
        for sentence in re.split(r'(?<=[.!?])\s+|\n+', text):
            sentence=sentence.strip()
            if sentence and any(t in sentence.lower() for t in terms):
                score=sum(sentence.lower().count(t) for t in terms)
                candidates.append((score, sentence, f"{r['original_name']} · page {r['page_number']}"))
    candidates.sort(key=lambda x:-x[0])
    answer_parts=[]; seen=set()
    for _,sentence,source in candidates:
        key=re.sub(r'\s+',' ',sentence.lower())
        if key in seen: continue
        seen.add(key)
        answer_parts.append({'text':sentence[:900],'source':source})
        if len(answer_parts)>=5: break
    source_count=(len((results or {}).get('notes',[]))+len((results or {}).get('pages',[]))) if results else 0
    return render(request,'assistant.html',{'query':q,'results':results,'answer_parts':answer_parts,'source_count':source_count})

@app.post('/audio-notes')
async def save_audio_note(request: Request, subject_id: int = Form(...), chapter_id: str = Form(''), title: str = Form(...), transcript: str = Form(...), audio: UploadFile = File(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    subject=db.get_subject(user['id'],subject_id)
    if not subject: raise HTTPException(404,'Subject not found.')
    chapter=int(chapter_id) if chapter_id.strip() else None
    if chapter:
        c=db.get_chapter(user['id'],chapter)
        if not c or c['subject_id'] != subject_id: raise HTTPException(400,'Invalid chapter.')
    clean_transcript=transcript.strip()
    if not clean_transcript: raise HTTPException(400,'Please record something or enter the transcript before saving.')
    data=await audio.read()
    if not data: raise HTTPException(400,'The audio recording is empty. Record again before saving.')
    if len(data)>15*1024*1024: raise HTTPException(413,'Audio is too large. Maximum size is 15 MB.')
    mime=audio.content_type or 'audio/webm'
    audio_id=db.add_audio_note(user['id'],subject_id,chapter,title.strip() or 'Voice note',clean_transcript,data,mime)
    if not audio_id: raise HTTPException(500,'The voice note could not be saved. Please try again.')
    return RedirectResponse(f'/chapters/{chapter}' if chapter else f'/subjects/{subject_id}',303)

@app.post('/audio-notes/{audio_id}/edit')
def edit_audio_note(request: Request, audio_id: int, title: str = Form(...), transcript: str = Form(...), csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    a=db.get_audio_note(user['id'],audio_id)
    if not a: raise HTTPException(404)
    if not title.strip() or not transcript.strip(): raise HTTPException(400,'Title and transcript cannot be empty.')
    db.update_note(user['id'],a['note_id'],title.strip(),transcript.strip())
    return RedirectResponse(f'/chapters/{a["chapter_id"]}' if a['chapter_id'] else f'/subjects/{a["subject_id"]}',303)

@app.post('/audio-notes/{audio_id}/delete')
def delete_audio_note(request: Request, audio_id: int, csrf_token: str = Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    a=db.get_audio_note(user['id'],audio_id)
    if not a: raise HTTPException(404)
    location=f'/chapters/{a["chapter_id"]}' if a['chapter_id'] else f'/subjects/{a["subject_id"]}'
    db.delete_note(user['id'],a['note_id'])
    return RedirectResponse(location,303)

@app.get('/audio-notes/{audio_id}/play')
def play_audio_note(request: Request, audio_id: int):
    user=require_user(request)
    audio=db.get_audio_note(user['id'],audio_id)
    if not audio: raise HTTPException(404)
    return Response(content=bytes(audio['data']), media_type=audio['mime_type'], headers={'Content-Disposition': 'inline'})

@app.get('/audio-notes/{audio_id}')
def open_audio_note(request: Request, audio_id: int):
    user=require_user(request); a=db.get_audio_note(user['id'],audio_id)
    if not a: raise HTTPException(404)
    return Response(content=bytes(a['data']),media_type=a['mime_type'])

@app.get('/files/{file_id}/open')
def open_file(request: Request, file_id: int):
    user=require_user(request); p=db.get_pdf(user['id'],file_id,True)
    if not p: raise HTTPException(404)
    mime={'pdf':'application/pdf','doc':'application/msword','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','ppt':'application/vnd.ms-powerpoint','pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation','jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif'}.get(p['file_type'],'application/octet-stream')
    # Keep Office files inline so browsers/extensions that support them can render
    # them in a new tab; otherwise the browser/OS may download them normally.
    disposition='inline'
    return Response(content=bytes(p['data']), media_type=mime, headers={'Content-Disposition': f"{disposition}; filename=\"{p['original_name'].replace(chr(34), '')}\""})

@app.get('/files/{file_id}/download')
def download_file(request: Request, file_id: int):
    user=require_user(request); p=db.get_pdf(user['id'],file_id,True)
    if not p: raise HTTPException(404)
    mime={'pdf':'application/pdf','doc':'application/msword','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','ppt':'application/vnd.ms-powerpoint','pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation','jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif'}.get(p['file_type'],'application/octet-stream')
    return Response(content=bytes(p['data']), media_type=mime, headers={'Content-Disposition': f"attachment; filename=\"{p['original_name'].replace(chr(34), '')}\""})

@app.get('/files/{file_id}/preview-page/{page_number}')
def office_preview_page(request: Request, file_id: int, page_number: int):
    user=require_user(request)
    p=db.get_pdf(user['id'],file_id,True)
    if not p: raise HTTPException(404)
    if p['file_type'] not in {'doc','docx','ppt','pptx'}:
        raise HTTPException(400,'Preview pages are available for Word and PowerPoint files.')
    cached=db.get_file_preview(user['id'],file_id)
    preview=bytes(cached['preview_data']) if cached and cached['preview_data'] else None
    count=int(cached['preview_page_count'] or 0) if cached else 0
    if not preview:
        preview,count=build_office_preview(bytes(p['data']), '.'+p['file_type'], p['original_name'])
        if preview:
            db.save_file_preview(user['id'],file_id,preview,count)
    if not preview or page_number < 1 or page_number > count:
        raise HTTPException(404,'Preview page not available.')
    try:
        import fitz
        doc=fitz.open(stream=preview,filetype='pdf')
        page=doc.load_page(page_number-1)
        pix=page.get_pixmap(matrix=fitz.Matrix(1.15,1.15), alpha=False)
        png=pix.tobytes('png')
        doc.close()
        return Response(content=png, media_type='image/png', headers={'Cache-Control':'private, max-age=300'})
    except Exception:
        raise HTTPException(503,'Unable to render this preview page.')

@app.get('/files/{file_id}/office-preview')
def office_preview(request: Request, file_id: int):
    user=require_user(request)
    p=db.get_pdf(user['id'],file_id,True)
    if not p: raise HTTPException(404)
    if p['file_type'] not in {'doc','docx','ppt','pptx'}:
        raise HTTPException(400,'Office preview is available for Word and PowerPoint files.')
    cached=db.get_file_preview(user['id'],file_id)
    preview=bytes(cached['preview_data']) if cached and cached['preview_data'] else None
    count=int(cached['preview_page_count'] or 0) if cached else 0
    if not preview:
        preview,count=build_office_preview(bytes(p['data']), '.'+p['file_type'], p['original_name'])
        if preview:
            db.save_file_preview(user['id'],file_id,preview,count)
    if not preview:
        raise HTTPException(503,'Office preview is not available on this server. Use Open original or Download.')
    return Response(content=preview, media_type='application/pdf', headers={'Content-Disposition': 'inline'})

@app.get('/files/{file_id}/view', response_class=HTMLResponse)
def view_file(request: Request, file_id: int):
    user=require_user(request); p=db.get_pdf(user['id'],file_id)
    if not p: raise HTTPException(404)
    if p['file_type'] == 'pdf':
        return RedirectResponse(f'/pdfs/{file_id}/viewer', 303)
    if p['file_type'] in {'jpg','jpeg','png','webp','gif'}:
        return render(request,'image_viewer.html',{'file':p})
    pages=db.get_pdf_pages(user['id'],file_id)
    preview=db.get_file_preview(user['id'],file_id) if p['file_type'] in {'doc','docx','ppt','pptx'} else None
    return render(request,'document_viewer.html',{'file':p,'pages':pages,'preview_available':bool(preview and preview['preview_data']),'preview_page_count':int((preview or {}).get('preview_page_count') or 0)})

@app.get('/pdfs/{pdf_id}/open')
def open_pdf(request: Request,pdf_id: int):
    user=require_user(request); p=db.get_pdf(user['id'],pdf_id,True)
    if not p: raise HTTPException(404)
    return Response(content=bytes(p['data']), media_type='application/pdf', headers={'Content-Disposition': f"inline; filename*=UTF-8''{p['original_name'].replace(chr(34), '')}"})

@app.get('/pdfs/{pdf_id}/viewer', response_class=HTMLResponse)
def pdf_viewer(request: Request, pdf_id: int, page: int = 1, q: str = ''):
    user=require_user(request)
    pdf=db.get_pdf(user['id'], pdf_id)
    if not pdf: raise HTTPException(404)
    pages=db.get_pdf_pages(user['id'], pdf_id)
    max_page=int(pdf['page_count'] or 1)
    safe_page=max(1,min(int(page),max_page))
    target=next((p for p in pages if int(p['page_number']) == safe_page), None)
    return render(request,'pdf_viewer.html',{'pdf':pdf,'pages':pages,'target':target,'query':q,'page_number':safe_page})

@app.get('/pdfs/{pdf_id}/text',response_class=HTMLResponse)
def pdf_text(request: Request,pdf_id:int):
    user=require_user(request); p=db.get_pdf(user['id'],pdf_id)
    if not p: raise HTTPException(404)
    return render(request,'pdf_text.html',{'pdf':p,'pages':db.get_pdf_pages(user['id'],pdf_id)})

@app.post('/pdfs/{pdf_id}/delete')
def remove_pdf(request: Request,pdf_id:int,csrf_token:str=Form(...)):
    user=require_user(request); require_csrf(request,csrf_token)
    p=db.get_pdf(user['id'],pdf_id)
    if not p: raise HTTPException(404)
    db.delete_pdf(user['id'],pdf_id)
    return RedirectResponse(f'/chapters/{p["chapter_id"]}' if p['chapter_id'] else f'/subjects/{p["subject_id"]}',303)

@app.get('/search',response_class=HTMLResponse)
def search(request: Request,q:str=''):
    user=require_user(request)
    results=db.search(user['id'],q) if q.strip() else None
    result_count=0 if results is None else sum(len(results[k]) for k in ('subjects','chapters','notes','pages'))
    return render(request,'search.html',dashboard_context(request,user,results,q) | {'result_count': result_count})
