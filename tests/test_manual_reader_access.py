"""Owner-only administration and document-level audience/activation boundaries."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import importlib.util
import re
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from reportlab.pdfgen import canvas
from flask import g
from flask.testing import FlaskClient

from app import create_app
from app.extensions import db
from app.models import (User, Role, Permission, Franchise, UserAccessProfile, UserModuleAccess,
                        MFFManual, MFFManualVersion, MFFIndexDocument, MFFManualAcknowledgement, AuditLog)
from app.manuals import access


@pytest.fixture
def app(tmp_path):
    class FreshLoginClient(FlaskClient):
        def open(self, *args, **kwargs):
            # The fixture keeps one app context open; real requests do not share g.
            g.pop("_login_user", None)
            return super().open(*args, **kwargs)
    class Config:
        SECRET_KEY = "manual-access-tests"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        TESTING = True
        MANUALS_STORAGE_ROOT = str(tmp_path / "storage")
    application = create_app(Config)
    application.test_client_class = FreshLoginClient
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


def user(email, role="Admin", manage=True):
    actions = ["view", "manage"] if manage else ["view"]
    permissions = []
    for action in actions:
        p = Permission.query.filter_by(code=f"manuals:{action}").first()
        if p is None:
            p = Permission(module="Manuals", action=action, code=f"manuals:{action}", label=action)
            db.session.add(p)
        permissions.append(p)
    r = Role.query.filter_by(name=role).first()
    if r is None:
        r = Role(name=role, permissions=permissions)
        db.session.add(r)
    result = User(name="Test", surname="Reader", email=email, password_hash="unused", roles=[r])
    db.session.add(result)
    db.session.commit()
    return result


def login(app, account, profile=None):
    client = app.test_client()
    with client.session_transaction() as state:
        state["_user_id"] = str(account.id)
        state["_fresh"] = True
        if profile:
            state["access_profile_key"] = profile
    return client


def manual(tmp_path, title="Sample procedure", audience="all", required_module=None):
    m = MFFManual(title=title, audience=audience, required_module=required_module)
    db.session.add(m)
    db.session.flush()
    path = tmp_path / f"manual-{m.id}.pdf"
    c = canvas.Canvas(str(path)); c.drawString(50, 700, f"{title} unique-manual-search"); c.save()
    for label, published in [("old", False), ("current", True)]:
        db.session.add(MFFManualVersion(manual=m, version_label=label, filename=path.name,
                                       storage_path=str(path), sha256="test", is_published=published))
    db.session.commit()
    return m


def csrf(client, url):
    response = client.get(url)
    assert response.status_code == 200, response.data
    return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()


@pytest.mark.parametrize("email", ["admin@example.com", "wjm+copy@martinsdirect.com", "wjm@martinsdirect.com.attacker.test"])
def test_only_exact_owner_identity_can_administer(app, tmp_path, email):
    account = user(email)
    m = manual(tmp_path)
    client = login(app, account)
    page = client.get(f"/manuals/{m.id}")
    assert page.status_code == 200
    assert b">Reader access<" not in page.data and b">Remove manual<" not in page.data
    for endpoint in ["access", "remove"]:
        assert client.get(f"/manuals/{m.id}/{endpoint}").status_code == 403
        assert client.post(f"/manuals/{m.id}/{endpoint}").status_code == 403
    assert m.removed_at is None and not AuditLog.query.count()


def test_case_insensitive_owner_and_csrf_confirmation(app, tmp_path):
    owner = user("WJM@MartinsDirect.com")
    m = manual(tmp_path)
    client = login(app, owner)
    assert b">Reader access<" in client.get(f"/manuals/{m.id}").data
    url = f"/manuals/{m.id}/remove"
    token = csrf(client, url)
    assert m.removed_at is None
    assert client.post(url, data={"confirm_remove": m.id}).status_code == 400
    assert client.post(url, data={"csrf_token": token, "confirm_remove": "wrong"}).status_code == 400
    assert client.post(f"/manuals/{m.id}/access", data={"audience": "all"}).status_code == 400
    assert m.removed_at is None


def test_removed_manual_disappears_everywhere_but_retains_audit(app, tmp_path):
    owner = user("wjm@martinsdirect.com")
    reader = user("holder@example.com", role="Franchise User", manage=False)
    m = manual(tmp_path, title="Duplicate administrator guide")
    keep = manual(tmp_path, title="Keep this guide")
    versions = list(m.versions)
    index = MFFIndexDocument(title="Duplicate linked index", manual=m, filename="index.pdf", storage_path=versions[0].storage_path)
    db.session.add_all([index, MFFManualAcknowledgement(manual_version=versions[0], user=reader, attested_name="Test reader")])
    db.session.commit()
    client = login(app, owner)
    url = f"/manuals/{m.id}/remove"
    token = csrf(client, url)
    assert client.post(url, data={"csrf_token": token, "confirm_remove": m.id}).status_code == 302
    assert m.removed_at is not None
    assert MFFManualAcknowledgement.query.count() == 1 and len(m.versions) == 2
    assert Path(versions[0].storage_path).is_file()
    assert AuditLog.query.filter_by(action="manual_removed", user_id=owner.id).count() == 1
    for account in [owner, reader]:
        viewer = login(app, account)
        for url in ["/manuals/", "/manuals/library", "/manuals/indexes", "/manuals/search?q=unique-manual-search"]:
            response = viewer.get(url)
            assert response.status_code == 200
            assert m.title.encode() not in response.data
            assert index.title.encode() not in response.data
        result = viewer.post("/manuals/search-pdfs", json={"query": "unique-manual-search"}).get_json()
        assert all(r["title"] not in {m.title, index.title} for r in result["results"])
        for version in versions:
            assert viewer.get(f"/manuals/version/{version.id}/view").status_code == 404
            assert viewer.get(f"/manuals/version/{version.id}/download").status_code == 404
            assert viewer.post(f"/manuals/version/{version.id}/acknowledge").status_code == 404
        assert viewer.get(f"/manuals/{m.id}").status_code == 404
        assert viewer.get(f"/manuals/{m.id}/open").status_code == 404
        assert viewer.get(f"/manuals/indexes/{index.id}/open").status_code == 404
        assert viewer.get(f"/manuals/{keep.id}").status_code == 200


def test_empty_visible_library_does_not_reseed_removed_documents(app, tmp_path, monkeypatch):
    owner = user("wjm@martinsdirect.com")
    m = manual(tmp_path)
    m.removed_at = datetime.now(timezone.utc); db.session.commit()
    seed = tmp_path / "seed"; (seed / "MANUALS").mkdir(parents=True)
    (seed / "MANUALS" / "Should not reappear.pdf").write_bytes(Path(m.versions[0].storage_path).read_bytes())
    monkeypatch.setattr("app.manuals.routes.seed_root", lambda app: seed)
    assert login(app, owner).get("/manuals/library").status_code == 200
    assert MFFManual.query.count() == 1


@pytest.mark.parametrize("number", range(1, 8))
def test_core_modules_are_compulsory_and_last_copy_is_protected(app, tmp_path, number):
    owner = user("wjm@martinsdirect.com")
    holder = user("holder@example.com", role="Franchise User", manage=False)
    m = manual(tmp_path, title=f"MODULE {number}", audience="selected", required_module="attendance:view")
    assert login(app, holder).get(f"/manuals/{m.id}").status_code == 200
    client = login(app, owner)
    token = csrf(client, f"/manuals/{m.id}/access")
    assert client.post(f"/manuals/{m.id}/access", data={"csrf_token": token, "audience": "selected"}).status_code == 400
    assert client.post(f"/manuals/{m.id}/remove", data={"csrf_token": token, "confirm_remove": m.id}).status_code == 400
    duplicate = manual(tmp_path, title=f"Module_{number}")
    assert client.post(f"/manuals/{m.id}/remove", data={"csrf_token": token, "confirm_remove": m.id}).status_code == 302
    assert login(app, holder).get(f"/manuals/{duplicate.id}").status_code == 200


def test_territory_needs_explicit_head_office_grant_and_rechecks_revocation(app, tmp_path):
    owner = user("wjm@martinsdirect.com")
    employee = user("finance@example.com", role="Finance Manager")
    holder = user("holder@example.com", role="Franchise User", manage=False)
    m = manual(tmp_path, title="Martins Territory Manual", audience="all")
    for account in [employee, holder]:
        client = login(app, account)
        assert client.get(f"/manuals/{m.id}").status_code == 404
        assert m.title.encode() not in client.get("/manuals/library").data
        assert not client.post("/manuals/search-pdfs", json={"query": "unique-manual-search"}).get_json()["results"]
    client = login(app, owner); url = f"/manuals/{m.id}/access"; token = csrf(client, url)
    assert client.post(url, data={"csrf_token": token, "audience": "all"}).status_code == 400
    assert client.post(url, data={"csrf_token": token, "audience": "selected", "reader_ids": holder.id}).status_code == 400
    assert client.post(url, data={"csrf_token": token, "audience": "selected", "reader_ids": employee.id}).status_code == 302
    assert login(app, employee).get(f"/manuals/{m.id}").status_code == 200
    # Even an erroneous direct database grant cannot grant a franchise identity Territory access.
    m.readers.append(holder); db.session.commit()
    assert login(app, holder).get(f"/manuals/{m.id}").status_code == 404
    assert client.post(url, data={"csrf_token": token, "audience": "selected"}).status_code == 302
    assert login(app, employee).get(f"/manuals/version/{m.versions[0].id}/download").status_code == 404


@pytest.mark.parametrize("code,title", [("attendance:view", "Attendance Module Manual"), ("heat_map:view", "Heat Map Module Manual")])
def test_optional_manuals_follow_holder_activation_and_expiry(app, tmp_path, code, title):
    holder = user("holder@example.com", role="Franchise User", manage=False)
    m = manual(tmp_path, title=title, audience="all")
    client = login(app, holder)
    assert client.get(f"/manuals/{m.id}").status_code == 404
    grant = UserModuleAccess(user=holder, module_code=code, is_enabled=True, trial_expires_at=datetime.now(timezone.utc)+timedelta(days=1))
    db.session.add(grant); db.session.commit()
    assert client.get(f"/manuals/{m.id}").status_code == 200
    assert client.post("/manuals/search-pdfs", json={"query": "unique-manual-search"}).get_json()["results"]
    grant.trial_expires_at = datetime.now(timezone.utc)-timedelta(days=1); db.session.commit()
    assert client.get(f"/manuals/version/{m.versions[0].id}/download").status_code == 404
    grant.paid_activated_at = datetime.now(timezone.utc); db.session.commit()
    assert client.get(f"/manuals/{m.id}").status_code == 200
    grant.is_enabled = False; db.session.commit()
    assert client.get(f"/manuals/{m.id}").status_code == 404


def test_profile_switch_never_inherits_owner_identity(app, tmp_path):
    owner = user("wjm@martinsdirect.com")
    other = user("other@example.com", role="Franchise User")
    f = Franchise(business_name="Test branch")
    grant = UserAccessProfile(identity_user_id=other.id, principal_user_id=owner.id, franchise=f)
    db.session.add(grant); db.session.commit()
    m = manual(tmp_path, title="Territory Manual")
    client = login(app, other, profile=f"branch:{grant.id}")
    assert client.get(f"/manuals/{m.id}").status_code == 404
    assert client.post(f"/manuals/{m.id}/remove").status_code == 403
    assert client.get(f"/manuals/{m.id}/access").status_code == 403


def test_upload_keeps_existing_access_and_new_manual_is_private(app, tmp_path):
    owner = user("wjm@martinsdirect.com")
    holder = user("holder@example.com", role="Franchise User", manage=False)
    m = manual(tmp_path, title="Office procedures", audience="head_office")
    data = Path(m.versions[0].storage_path).read_bytes()
    client = login(app, owner)
    assert client.post("/manuals/upload", data={"title": m.title, "version_label": "new", "file": (BytesIO(data), "new.pdf")}).status_code == 302
    assert m.audience == "head_office" and len(m.versions) == 3
    assert client.post("/manuals/upload", data={"title": "New confidential guide", "version_label": "v1", "file": (BytesIO(data), "new.pdf")}).status_code == 302
    new = MFFManual.query.filter_by(title="New confidential guide").one()
    assert new.audience == "selected"
    assert login(app, holder).get(f"/manuals/{new.id}").status_code == 404


def test_upgrade_protects_existing_territory_and_maps_optional_manuals():
    path = Path(__file__).parents[1]/"migrations/versions/v140_manual_owner_removal.py"
    spec = importlib.util.spec_from_file_location("manual_migration", path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE mff_manuals (id INTEGER PRIMARY KEY, title VARCHAR(255))")
        titles = [*(f"MODULE {n}" for n in range(1,8)), "Martins_Territory_Manual", "Administrator Manual", "Heat Map Module Manual", "Attendance Module Manual", "Insurance Claims Manual"]
        for title in titles:connection.execute(sa.text("INSERT INTO mff_manuals(title) VALUES (:title)"), {"title": title})
        migration.op = Operations(MigrationContext.configure(connection)); migration.upgrade()
        rows = {row["title"]: row for row in connection.execute(sa.text("SELECT * FROM mff_manuals")).mappings()}
        assert rows["Martins_Territory_Manual"]["audience"] == "selected"
        assert rows["Administrator Manual"]["audience"] == "head_office"
        assert rows["Heat Map Module Manual"]["required_module"] == "heat_map:view"
        assert rows["Attendance Module Manual"]["required_module"] == "attendance:view"
        assert rows["Insurance Claims Manual"]["required_module"] == "insurance_claims:view"
        assert all(rows[f"MODULE {n}"]["audience"] == "all" for n in range(1,8))
        migration.downgrade()
        assert {c["name"] for c in sa.inspect(connection).get_columns("mff_manuals")} == {"id", "title"}


@pytest.mark.parametrize("field", ["is_active", "is_active_account"])
def test_inactive_owner_cannot_remove_or_change_readers(app, tmp_path, field):
    owner = user("wjm@martinsdirect.com")
    m = manual(tmp_path)
    setattr(owner, field, False); db.session.commit()
    client = login(app, owner)
    for endpoint in ["access", "remove"]:
        assert client.post(f"/manuals/{m.id}/{endpoint}", data={"confirm_remove": m.id}).status_code in (302, 403)
    assert m.removed_at is None and not AuditLog.query.count()


def test_audience_applies_to_indexes_old_versions_and_search(app, tmp_path):
    owner = user("wjm@martinsdirect.com")
    employee = user("finance@example.com", role="Finance Assistant")
    holder = user("holder@example.com", role="Franchise User", manage=False)
    m = manual(tmp_path, title="Head Office instructions", audience="head_office")
    index = MFFIndexDocument(title="Private procedure index", manual=m, filename="index.pdf", storage_path=m.versions[0].storage_path)
    db.session.add(index); db.session.commit()
    viewer = login(app, holder)
    assert index.title.encode() not in viewer.get("/manuals/indexes").data
    assert viewer.get(f"/manuals/indexes/{index.id}/open").status_code == 404
    for version in m.versions:
        assert viewer.get(f"/manuals/version/{version.id}/download").status_code == 404
        assert viewer.post(f"/manuals/version/{version.id}/acknowledge").status_code == 404
    assert viewer.post("/manuals/search-pdfs", json={"query": "unique-manual-search"}).get_json()["results"] == []
    assert login(app, employee).get(f"/manuals/indexes/{index.id}/open").status_code == 200
    controller = login(app, owner); url = f"/manuals/{m.id}/access"; token = csrf(controller, url)
    assert controller.post(url, data={"csrf_token": token, "audience": "selected", "reader_ids": holder.id}).status_code == 302
    assert login(app, holder).get(f"/manuals/indexes/{index.id}/open").status_code == 200
    assert login(app, employee).get(f"/manuals/{m.id}").status_code == 404


def test_owner_cannot_disable_required_optional_module_gate(app, tmp_path):
    owner = user("wjm@martinsdirect.com")
    m = manual(tmp_path, title="Attendance Module Manual")
    controller = login(app, owner); url = f"/manuals/{m.id}/access"; token = csrf(controller, url)
    assert controller.post(url, data={"csrf_token": token, "audience": "all", "required_module": ""}).status_code == 400
    assert controller.post(url, data={"csrf_token": token, "audience": "all", "required_module": "attendance:view"}).status_code == 302
    assert m.required_module == "attendance:view"
