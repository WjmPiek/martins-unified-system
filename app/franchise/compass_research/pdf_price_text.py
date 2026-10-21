"""Isolated, time-bounded text extraction for public marketing PDFs (no OCR)."""
import io, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'vendor'))
from pypdf import PdfReader

if __name__ == '__main__':
    reader = PdfReader(io.BytesIO(sys.stdin.buffer.read(3000001)))
    if reader.is_encrypted:
        raise ValueError('Encrypted brochure requires manual review')
    parts = []
    for index in range(min(8, len(reader.pages))):
        page = reader.pages[index]
        if len(parts) and sum(map(len, parts)) > 150000:
            break
        parts.append((page.extract_text() or '')[:50000])
    print(json.dumps({'text': '\n'.join(parts)[:200000], 'pages': min(8, len(reader.pages)), 'limited': len(reader.pages) > 8}))
