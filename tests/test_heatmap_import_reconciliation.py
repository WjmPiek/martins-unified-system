from io import BytesIO

from flask_login import login_user, logout_user
from openpyxl import Workbook

from app import create_app
from app.extensions import db
from app.heatmap.routes import number, parse_heatmap_excel, reconcile_heatmap_records
from app.models import Franchise, HeatmapRecord, Permission, Role, User, UserModuleAccess, user_franchises


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


def _record(franchise_id, mf_file, *, relation="MAP:insurance_clients", city="Germiston", contact=""):
    return HeatmapRecord(
        franchise_id=franchise_id,
        mf_file=mf_file,
        deceased_name="Client",
        deceased_surname="One",
        city=city,
        province="Gauteng",
        relation=relation,
        contact_number=contact,
        source_filename="clients.xlsx",
    )


def test_reimport_updates_changed_client_and_skips_unchanged_client():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(business_name="Germiston")
        db.session.add(franchise)
        db.session.flush()

        assert reconcile_heatmap_records([_record(franchise.id, "MF-100")], franchise.id) == (1, 0, 0, 0)
        db.session.commit()

        changed = _record(franchise.id, "MF-100", city="Boksburg", contact="0820000000")
        assert reconcile_heatmap_records([changed], franchise.id) == (0, 1, 0, 0)
        db.session.commit()
        saved = HeatmapRecord.query.one()
        assert saved.city == "Boksburg"
        assert saved.contact_number == "0820000000"

        unchanged = _record(franchise.id, "MF-100", city="Boksburg", contact="0820000000")
        assert reconcile_heatmap_records([unchanged], franchise.id) == (0, 0, 1, 0)
        db.session.commit()
        assert HeatmapRecord.query.count() == 1


def test_reimport_removes_existing_exact_duplicates():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(business_name="Germiston")
        db.session.add(franchise)
        db.session.flush()
        db.session.add_all([
            _record(franchise.id, "MF-200"),
            _record(franchise.id, "MF-200"),
        ])
        db.session.commit()

        result = reconcile_heatmap_records([_record(franchise.id, "MF-200")], franchise.id)
        db.session.commit()

        assert result == (0, 0, 1, 1)
        assert HeatmapRecord.query.count() == 1


def test_wide_import_keeps_distinct_categories_for_same_mf_file():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(business_name="Germiston")
        db.session.add(franchise)
        db.session.flush()
        records = [
            _record(franchise.id, "MF-300", relation="MAP:deceased"),
            _record(franchise.id, "MF-300", relation="MAP:church"),
        ]

        assert reconcile_heatmap_records(records, franchise.id) == (2, 0, 0, 0)
        db.session.commit()
        assert HeatmapRecord.query.count() == 2


def test_deceased_reimport_removes_stale_imports_but_preserves_insurance_and_manual_locations():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(business_name="Northcliff (F)")
        db.session.add(franchise)
        db.session.flush()
        current_nok = _record(franchise.id, "MF-801", relation="MAP:next_of_kin")
        stale_nok = _record(franchise.id, "MF-OLD", relation="MAP:next_of_kin")
        insurance = _record(franchise.id, "MEM-801", relation="MAP:insurance_clients")
        manual = _record(franchise.id, "MANUAL-801", relation="MAP:church")
        current_nok.source_filename = "old-deceased.xlsx"
        stale_nok.source_filename = "old-deceased.xlsx"
        insurance.source_filename = "insurance.xlsx"
        manual.source_filename = ""
        db.session.add_all([current_nok, stale_nok, insurance, manual])
        db.session.commit()

        incoming = _record(franchise.id, "MF-801", relation="MAP:next_of_kin", city="Randburg")
        incoming.source_filename = "current-deceased.xlsx"
        result = reconcile_heatmap_records([incoming], franchise.id)
        db.session.commit()

        assert result == (0, 1, 0, 1)
        identities = {
            (record.mf_file, record.map_record_type, record.city)
            for record in HeatmapRecord.query.order_by(HeatmapRecord.id).all()
        }
        assert ("MF-801", "next_of_kin", "Randburg") in identities
        assert not any(item[0] == "MF-OLD" for item in identities)
        assert any(item[0] == "MEM-801" for item in identities)
        assert any(item[0] == "MANUAL-801" for item in identities)


def test_mem_client_file_is_imported_as_insurance_client():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(business_name="Germiston")
        user = User(name="Owner", surname="User", email="owner@example.com", password_hash="x")
        db.session.add_all([franchise, user])
        db.session.commit()

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["MF File", "Name", "Surname", "Relation", "Town", "Province"])
        sheet.append(["MF-400", "Client", "Four", "MEM", "Germiston", "Gauteng"])
        stream = BytesIO()
        workbook.save(stream)
        stream.seek(0)

        with app.test_request_context("/heat-map/import", method="POST"):
            login_user(user)
            records, skipped_non_mem, skipped_blank = parse_heatmap_excel(
                stream, "clients.xlsx", franchise.id
            )
            logout_user()

        assert skipped_non_mem == 0
        assert skipped_blank == 0
        assert len(records) == 1
        assert records[0].relation == "MAP:insurance_clients"
        assert records[0].map_record_type == "insurance_clients"


def test_migration_normalizes_clients_and_removes_existing_duplicates():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from migrations.versions import v124_heatmap_client_categories as migration

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(business_name="Germiston")
        db.session.add(franchise)
        db.session.flush()
        db.session.add_all([
            _record(franchise.id, "MF-500", relation="MEM", city="Old Town"),
            _record(franchise.id, "MF-500", relation="MEM", city="New Town"),
        ])
        db.session.commit()

        with db.engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()

        db.session.expire_all()
        records = HeatmapRecord.query.all()
        assert len(records) == 1
        assert records[0].relation == "MAP:insurance_clients"
        assert records[0].city == "New Town"


def test_heatmap_offers_separate_insurance_and_deceased_downloads():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        permission = Permission(
            module="Heat Map", action="view", code="heat_map:view", label="View Heat Map"
        )
        role = Role(name="Admin", permissions=[permission])
        admin = User(
            name="Admin", surname="User", email="admin@example.com",
            password_hash="x", roles=[role],
        )
        db.session.add(admin)
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
            session["_fresh"] = True

        page = client.get("/heat-map/")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "Download Insurance Members File" in html
        assert "Download Deceased Information File" in html

        insurance = client.get("/heat-map/template/insurance-members")
        deceased = client.get("/heat-map/template/deceased-information")
        assert insurance.status_code == 200
        assert deceased.status_code == 200
        assert "martins-insurance-members-heat-map-template.xlsx" in insurance.headers["Content-Disposition"]
        assert "martins-deceased-information-heat-map-template.xlsx" in deceased.headers["Content-Disposition"]
        assert insurance.data.startswith(b"PK")
        assert deceased.data.startswith(b"PK")


def test_franchise_user_import_is_immediately_visible_in_own_heatmap_data():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        permission = Permission(
            module="Heat Map", action="view", code="heat_map:view", label="View Heat Map"
        )
        role = Role(name="Franchise User", permissions=[permission])
        franchise = Franchise(business_name="Northcliff (F)")
        owner = User(
            name="Northcliff", surname="Owner", email="northcliff@example.com",
            password_hash="x", roles=[role], assigned_franchises=[franchise],
        )
        db.session.add_all([franchise, owner])
        db.session.flush()
        db.session.execute(
            user_franchises.update()
            .where(user_franchises.c.user_id == owner.id)
            .where(user_franchises.c.franchise_id == franchise.id)
            .values(is_primary=True)
        )
        db.session.add(UserModuleAccess(
            user_id=owner.id, module_code="heat_map:view", is_enabled=True
        ))
        db.session.commit()

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["MF File", "Deceased Name", "Deceased Surname", "DOD", "Address", "City", "Province"])
        sheet.append(["MF-600", "Person", "Six", "2026-06-01", "1 Main Road", "Northcliff", "Gauteng"])
        stream = BytesIO()
        workbook.save(stream)
        stream.seek(0)

        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(owner.id)
            session["_fresh"] = True

        response = client.post(
            "/heat-map/import",
            data={"franchise_id": str(franchise.id), "file": (stream, "deceased.xlsx")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 302
        assert f"franchise_id={franchise.id}" in response.headers["Location"]
        saved = HeatmapRecord.query.one()
        assert saved.franchise_id == franchise.id

        data_response = client.get(f"/heat-map/data?franchise_id={franchise.id}")
        assert data_response.status_code == 200
        payload = data_response.get_json()
        assert payload["summary"]["total"] == 1
        assert len(payload["records"]) == 1
        assert payload["records"][0]["franchiseName"] == "Northcliff (F)"


def test_migration_assigns_unscoped_import_to_creator_primary_franchise():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from migrations.versions import v125_assign_unscoped_heatmap_records as migration

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(business_name="Northcliff (F)")
        owner = User(
            name="Northcliff", surname="Owner", email="owner2@example.com", password_hash="x",
            assigned_franchises=[franchise],
        )
        db.session.add_all([franchise, owner])
        db.session.flush()
        db.session.execute(
            user_franchises.update()
            .where(user_franchises.c.user_id == owner.id)
            .where(user_franchises.c.franchise_id == franchise.id)
            .values(is_primary=True)
        )
        record = HeatmapRecord(
            franchise_id=None,
            created_by_id=owner.id,
            mf_file="MF-700",
            deceased_name="Unscoped",
            relation="MAP:deceased",
        )
        db.session.add(record)
        db.session.commit()
        record_id = record.id

        with db.engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()

        db.session.expire_all()
        assert db.session.get(HeatmapRecord, record_id).franchise_id == franchise.id


def test_heatmap_data_rejects_non_finite_numbers_and_serializes_legacy_values():
    assert number("NaN") is None
    assert number("Infinity") is None
    assert number("-Infinity") is None

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        permission = Permission(
            module="Heat Map", action="view", code="heat_map:view", label="View Heat Map"
        )
        role = Role(name="Admin", permissions=[permission])
        franchise = Franchise(business_name="Northcliff (F)")
        admin = User(
            name="Admin", surname="User", email="finite@example.com",
            password_hash="x", roles=[role], assigned_franchises=[franchise],
        )
        record = HeatmapRecord(
            franchise=franchise, mf_file="MF-BAD-NUMBER", deceased_name="Legacy",
            latitude=float("inf"), longitude=float("-inf"), weight=float("nan"),
        )
        db.session.add_all([admin, record])
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
            session["_fresh"] = True

        response = client.get(f"/heat-map/data?franchise_id={franchise.id}")
        assert response.status_code == 200
        assert b"Infinity" not in response.data
        assert b"NaN" not in response.data
        payload = response.get_json()
        assert payload["records"][0]["latitude"] is None
        assert payload["records"][0]["longitude"] is None
        assert payload["records"][0]["weight"] == 1
