import pymupdf as fitz
from pathlib import Path

pdf_path = Path("downloads/2026-09-09.pdf")
if not pdf_path.exists():
    # Ищем любой файл с 2026-09 в имени
    for f in Path("downloads").glob("2026-09*.pdf"):
        pdf_path = f
        break

print(f"Файл: {pdf_path}")
doc = fitz.open(str(pdf_path))
for page in doc:
    print(page.get_text())
doc.close()
#uequaldeqjbrjrsg