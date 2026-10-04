from pathlib import Path

from docx import Document
from pptx import Presentation

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

from pypdf import PdfReader

try:
    import pdfplumber
except Exception:
    pdfplumber = None


def _text_quality(text: str):
    import re
    text = text or ''
    suspicious = text.count('�') + len(re.findall(r'\?{2,}', text)) + text.count('\x00')
    printable = sum(ch.isprintable() for ch in text)
    letters = sum(ch.isalpha() for ch in text)
    words = len(text.split())
    return (-(suspicious / max(1, len(text))), printable / max(1, len(text)), words, letters)


def extract_pdf_pages(path: Path):
    # Use several independent extractors. PDF font maps are notoriously
    # inconsistent; choosing the cleanest page text prevents many stray ?/�
    # characters from becoming part of search results.
    fitz_pages=[]; pypdf_pages=[]; plumber_pages=[]
    if fitz is not None:
        try:
            doc=fitz.open(str(path))
            fitz_pages=[(i+1,(page.get_text("text",sort=True) or "").strip()) for i,page in enumerate(doc)]
            doc.close()
        except Exception:
            fitz_pages=[]
    try:
        reader=PdfReader(str(path))
        pypdf_pages=[(i+1,(page.extract_text() or "").strip()) for i,page in enumerate(reader.pages)]
    except Exception:
        pypdf_pages=[]
    if pdfplumber is not None:
        try:
            with pdfplumber.open(str(path)) as pdf:
                plumber_pages=[(i+1,(page.extract_text(x_tolerance=2,y_tolerance=3) or "").strip()) for i,page in enumerate(pdf.pages)]
        except Exception:
            plumber_pages=[]
    count=max(len(fitz_pages),len(pypdf_pages),len(plumber_pages))
    chosen=[]
    for i in range(count):
        candidates=[]
        for pages in (fitz_pages,pypdf_pages,plumber_pages):
            if i<len(pages) and pages[i][1]: candidates.append(pages[i][1])
        text=max(candidates,key=_text_quality) if candidates else ''
        chosen.append((i+1,text))
    return chosen

def extract_document(path: Path):
    ext = path.suffix.lower()
    if ext == '.pdf':
        return extract_pdf_pages(path)
    if ext == '.docx':
        doc = Document(str(path))
        text = []
        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                text.append(t)
        for table in doc.tables:
            for row in table.rows:
                vals = [c.text.strip() for c in row.cells]
                if any(vals):
                    text.append(' | '.join(vals))
        return [(1, '\n'.join(text))] if text else [(1, '')]
    if ext == '.pptx':
        prs = Presentation(str(path))
        pages = []
        for i, slide in enumerate(prs.slides, 1):
            bits = []
            for shape in slide.shapes:
                if hasattr(shape, 'text') and shape.text.strip():
                    bits.append(shape.text.strip())
                if getattr(shape, 'has_table', False):
                    for row in shape.table.rows:
                        bits.append(' | '.join(cell.text.strip() for cell in row.cells))
            pages.append((i, '\n'.join(bits)))
        return pages or [(1, '')]
    raise ValueError('Unsupported file type')
