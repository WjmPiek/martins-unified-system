from flask import g

from app import create_app
from app.extensions import db
from app.models import Franchise, Role, User, user_franchises


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


def test_admin_creates_brand_new_franchise_user_from_franchise_details():
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

        form_page = client.get("/franchise/details/new-franchise-user")
        assert form_page.status_code == 200
        html = form_page.get_data(as_text=True)
        assert "Franchise Details — Create New Franchise User" in html
        assert "Create Franchise and User" in html

        response = client.post(
            "/franchise/details/new-franchise-user",
            data={
                "user_name": "Yolandi",
                "user_surname": "Heyns",
                "user_email": "yolandi@example.com",
                "password": "temporary-password",
                "business_name": "Panorama",
                "franchise_code": "PAN001",
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

        g.pop("accessible_franchises_cache", None)
        details = client.get(f"/franchise/details?franchise_id={franchise.id}")
        details_html = details.get_data(as_text=True)
        assert details.status_code == 200
        assert "yolandi@example.com" in details_html

        g.pop("accessible_franchises_cache", None)
        users_page = client.get("/admin/franchise-users")
        assert users_page.status_code == 200
        assert "yolandi@example.com" in users_page.get_data(as_text=True)
