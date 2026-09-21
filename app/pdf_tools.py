from pypdf import PdfReader

def extract_pages(path):
    reader=PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("This PDF is password-protected.")
    pages=[]
    for page in reader.pages:
        try:
            text=page.extract_text() or ""
        except Exception:
            text=""
        pages.append(text.strip())
    return pages
