from pathlib import Path

from docx import Document
from pptx import Presentation

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

from pypdf import PdfReader


def _text_quality(text: str):
    text = text or ''
    bad = text.count('�') + text.count('?')
    letters = sum(ch.isalpha() for ch in text)
    words = len(text.split())
    # Prefer text with fewer suspicious replacement/question marks, then more words.
    return (-(bad / max(1, len(text))), words, letters)


def extract_pdf_pages(path: Path):
    # PDFs can contain broken/embedded font encodings. Different extractors can
    # produce very different results for the same page, so compare both when
    # possible instead of accepting the first non-empty result.
    fitz_pages = []
    pypdf_pages = []
    if fitz is not None:
        try:
            doc = fitz.open(str(path))
            fitz_pages = [(i + 1, (page.get_text("text", sort=True) or "").strip()) for i, page in enumerate(doc)]
            doc.close()
        except Exception:
            fitz_pages = []
    try:
        reader = PdfReader(str(path))
        pypdf_pages = [(i + 1, (page.extract_text() or "").strip()) for i, page in enumerate(reader.pages)]
    except Exception:
        pypdf_pages = []
    count=max(len(fitz_pages), len(pypdf_pages))
    chosen=[]
    for i in range(count):
        a=fitz_pages[i][1] if i < len(fitz_pages) else ''
        b=pypdf_pages[i][1] if i < len(pypdf_pages) else ''
        if not a:
            text=b
        elif not b:
            text=a
        else:
            # When one extractor has a broken font map, its output often contains
            # many '?' or replacement characters. Pick the cleaner representation.
            text=max((a,b), key=_text_quality)
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
