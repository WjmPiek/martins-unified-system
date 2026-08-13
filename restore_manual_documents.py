"""Restore bundled Manuals PDF files after a database-only restore.

The old Manuals database contains the document metadata but not the PDFs themselves.
This command reconnects those entries to the PDFs supplied in app/manuals_seed.
It never deletes or replaces an existing readable file.
"""

from pathlib import Path
import shutil

from app import create_app
from app.extensions import db
from app.models import MFFIndexDocument, MFFManual, MFFManualVersion
from app.manuals.routes import _seed_if_empty
from app.manuals.storage import ensure_pdf_storage, resolve_storage_path, seed_root


def find_seed_file(root, filename, folder):
    if not filename:
        return None
    wanted = filename.casefold()
    for candidate in (root / folder).glob("*.pdf"):
        if candidate.name.casefold() == wanted:
            return candidate
    return None


def copy_and_link(app, item, source, folder):
    destination_dir = ensure_pdf_storage(app) / folder
    destination = destination_dir / source.name
    if not destination.exists():
        shutil.copy2(source, destination)
    item.filename = destination.name
    item.storage_path = str(destination)


def main():
    app = create_app()
    with app.app_context():
        _seed_if_empty()
        root = seed_root(app)
        repaired_versions = 0
        repaired_indexes = 0
        unavailable_versions = 0
        unavailable_indexes = 0

        for version in MFFManualVersion.query.order_by(MFFManualVersion.id).all():
            current = resolve_storage_path(app, version.storage_path)
            if current and current.exists():
                continue
            source = find_seed_file(root, version.filename, "MANUALS")
            if source:
                copy_and_link(app, version, source, "manuals")
                repaired_versions += 1
            else:
                unavailable_versions += 1

        for document in MFFIndexDocument.query.order_by(MFFIndexDocument.id).all():
            current = resolve_storage_path(app, document.storage_path)
            if current and current.exists():
                continue
            source = find_seed_file(root, document.filename, "INDEX")
            if source:
                copy_and_link(app, document, source, "indexes")
                repaired_indexes += 1
            else:
                unavailable_indexes += 1

        db.session.commit()
        print(
            "Manual documents restored: "
            f"{repaired_versions} manual PDFs, {repaired_indexes} index PDFs. "
            f"Not found in the included bundle: {unavailable_versions} manuals, {unavailable_indexes} indexes."
        )


if __name__ == "__main__":
    main()
