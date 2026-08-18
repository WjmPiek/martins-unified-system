from io import BytesIO

from flask_login import login_user, logout_user
from openpyxl import Workbook

from app import create_app
from app.extensions import db
from app.heatmap.routes import parse_heatmap_excel, reconcile_heatmap_records
from app.models import Franchise, HeatmapRecord, Permission, Role, User


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
