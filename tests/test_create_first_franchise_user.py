from app import create_app
from app.extensions import db
from app.models import Franchise, Role, User, user_franchises


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


def test_admin_can_create_first_franchise_and_user_together():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        admin_role = Role(name="Admin")
        franchise_role = Role(name="Franchise User")
        admin = User(
            name="Admin",
            surname="User",
            email="admin@example.com",
            password_hash="x",
            roles=[admin_role],
        )
        db.session.add_all([admin, franchise_role])
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
            session["_fresh"] = True

        form_page = client.get("/admin/users/create")
        assert form_page.status_code == 200
        html = form_page.get_data(as_text=True)
        assert "Create First Franchise" in html
        assert "franchise-scope-options" in html

        response = client.post(
            "/admin/users/create",
            data={
                "name": "Yolandi",
                "surname": "Heyns",
                "email": "yolandi@example.com",
                "password": "temporary-password",
                "role_id": str(franchise_role.id),
                "create_new_franchise": "1",
                "new_franchise_name": "Panorama",
                "new_franchise_code": "PAN001",
            },
        )
        assert response.status_code == 302

        franchise = Franchise.query.filter_by(business_name="Panorama").one()
        owner = User.query.filter_by(email="yolandi@example.com").one()
        assert owner.assigned_franchise_id() == franchise.id
        assert owner.can_access_franchise(franchise.id)
        link = db.session.execute(
            db.select(user_franchises)
            .where(user_franchises.c.user_id == owner.id)
            .where(user_franchises.c.franchise_id == franchise.id)
        ).one()
        assert link.is_primary is True
