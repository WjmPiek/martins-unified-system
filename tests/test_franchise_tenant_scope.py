from flask import g
from flask_login import login_user, logout_user

from app import create_app
from app.admin.routes import (
    ensure_own_primary_franchise_link,
    normalise_user_scope_for_role,
    set_primary_franchise_link,
)
from app.extensions import db
from app.franchise.routes import accessible_franchises_for_current_user
from app.franchise_context import get_accessible_franchises
from app.models import Franchise, MonthlyFigure, Permission, Role, User, user_franchises
from app.royalties.routes import get_figures, get_ordered_linked_franchises_for_user


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


def test_admin_sees_every_franchise_but_owner_is_limited_to_primary():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        admin_role = Role(name="Admin")
        owner_role = Role(name="Franchise User")
        middelburg = Franchise(business_name="Middelburg", is_performance_active=False)
        other = Franchise(business_name="Other Franchise", is_performance_active=True)
        admin = User(name="Admin", surname="User", email="admin@example.com", password_hash="x", roles=[admin_role])
        owner = User(name="Middelburg", surname="User", email="middelburg@example.com", password_hash="x", roles=[owner_role])
        owner.assigned_franchises = [middelburg, other]
        db.session.add_all([admin, owner])
        db.session.flush()
        db.session.execute(
            user_franchises.update()
            .where(user_franchises.c.user_id == owner.id)
            .where(user_franchises.c.franchise_id == middelburg.id)
            .values(is_primary=True)
        )
        db.session.commit()

        with app.test_request_context("/franchise/details"):
            login_user(admin)
            assert {item.id for item in get_accessible_franchises()} == {middelburg.id, other.id}
            logout_user()

        with app.test_request_context("/franchise/details"):
            g.pop("accessible_franchises_cache", None)
            login_user(owner)
            assert [item.id for item in get_accessible_franchises()] == [middelburg.id]
            assert [item.id for item in accessible_franchises_for_current_user()] == [middelburg.id]
            assert owner.can_access_franchise(middelburg.id)
            assert not owner.can_access_franchise(other.id)
            logout_user()


def test_royalty_group_does_not_remove_linked_branch_owner():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        owner_role = Role(name="Franchise User")
        main = Franchise(business_name="Main")
        linked = Franchise(business_name="Linked")
        main_owner = User(name="Main", surname="User", email="main@example.com", password_hash="x", roles=[owner_role])
        linked_owner = User(name="Linked", surname="User", email="linked@example.com", password_hash="x", roles=[owner_role])
        db.session.add_all([main, linked, main_owner, linked_owner])
        db.session.flush()
        ensure_own_primary_franchise_link(main_owner, main)
        ensure_own_primary_franchise_link(linked_owner, linked)

        set_primary_franchise_link(main_owner, main, [main, linked])
        db.session.flush()

        links = db.session.execute(db.select(user_franchises)).all()
        assert (linked_owner.id, linked.id, True) in links
        assert (main_owner.id, main.id, True) in links
        assert (main_owner.id, linked.id, False) in links
        assert main_owner.assigned_franchise_id() == main.id
        assert linked_owner.assigned_franchise_id() == linked.id


def test_grouped_main_franchise_user_keeps_one_primary_and_can_log_in():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        owner_role = Role(name="Franchise User")
        centurion = Franchise(business_name="Centurion")
        grouped_branch = Franchise(business_name="Grouped Branch")
        owner = User(
            name="Centurion",
            surname="User",
            email="centurion@martinsdirect.com",
            password_hash="x",
            roles=[owner_role],
        )
        owner.set_password("secure-password")
        db.session.add_all([centurion, grouped_branch, owner])
        db.session.flush()
        set_primary_franchise_link(owner, centurion, [centurion, grouped_branch])
        db.session.commit()

        ok, message = normalise_user_scope_for_role(
            owner,
            "Franchise User",
            [centurion.id, grouped_branch.id],
        )
        db.session.commit()

        assert ok is True
        assert message == ""
        assert {item.id for item in owner.assigned_franchises} == {centurion.id, grouped_branch.id}
        primary_links = db.session.execute(
            db.select(user_franchises.c.franchise_id)
            .where(user_franchises.c.user_id == owner.id)
            .where(user_franchises.c.is_primary == True)
        ).scalars().all()
        assert primary_links == [centurion.id]
        assert owner.assigned_franchise_id() == centurion.id

        client = app.test_client()
        response = client.post(
            "/login",
            data={"email": "centurion@martinsdirect.com", "password": "secure-password"},
        )
        assert response.status_code == 302
        with client.session_transaction() as session:
            assert session.get("_user_id") == str(owner.id)
            messages = [message for _category, message in session.get("_flashes", [])]
        assert "A Franchise User must be linked to exactly one primary franchise." not in messages


def test_grouped_main_franchise_user_royalty_scope_includes_linked_branches():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        owner_role = Role(name="Franchise User")
        main = Franchise(business_name="Centurion")
        grouped_branch = Franchise(business_name="Grouped Branch")
        owner = User(
            name="Centurion",
            surname="User",
            email="centurion@martinsdirect.com",
            password_hash="x",
            roles=[owner_role],
        )
        db.session.add_all([main, grouped_branch, owner])
        db.session.flush()
        set_primary_franchise_link(owner, main, [main, grouped_branch])
        db.session.add_all([
            MonthlyFigure(
                franchise=main,
                month=6,
                year=2026,
                funeral_receipts=100,
            ),
            MonthlyFigure(
                franchise=grouped_branch,
                month=6,
                year=2026,
                funeral_receipts=200,
            ),
        ])
        db.session.commit()

        with app.test_request_context("/royalties/?month=6&year=2026"):
            login_user(owner)
            # Normal application access stays on the primary franchise only.
            assert [item.id for item in owner.accessible_franchises()] == [main.id]
            # The Royalty page receives the full explicitly linked billing group.
            royalty_scope = get_ordered_linked_franchises_for_user(owner)
            assert [item.id for item in royalty_scope] == [main.id, grouped_branch.id]
            figures, selected, linked, show_all, month, year = get_figures()
            assert (month, year) == (6, 2026)
            assert selected.id == main.id
            assert [item.id for item in linked] == [main.id, grouped_branch.id]
            assert show_all is False
            assert len(figures) == 3
            assert figures[0].is_grouped_summary is True
            assert figures[0].source_branch_names == ["Centurion", "Grouped Branch"]
            assert {item.franchise.business_name for item in figures[1:]} == {
                "Centurion",
                "Grouped Branch",
            }
            logout_user()


def test_inactive_business_overview_selection_opens_the_same_details_record():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        view_details = Permission(
            module="Franchise Details",
            action="view",
            code="franchise_details:view",
            label="View Franchise Details",
        )
        admin_role = Role(name="Admin", permissions=[view_details])
        middelburg = Franchise(business_name="Middelburg", is_performance_active=False)
        other = Franchise(business_name="Other Franchise", is_performance_active=True)
        admin = User(name="Admin", surname="User", email="admin@example.com", password_hash="x", roles=[admin_role])
        db.session.add_all([middelburg, other, admin])
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
            session["_fresh"] = True

        selected = client.get(f"/dashboard/select-franchise/{middelburg.id}")
        assert selected.status_code == 302
        details = client.get("/franchise/details")
        html = details.get_data(as_text=True)
        assert details.status_code == 200
        assert "Middelburg" in html
        assert "Other Franchise" in html
        assert f'value="{middelburg.id}"' in html


def test_admin_franchise_users_page_keeps_no_data_owner_visible():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        view_users = Permission(
            module="Users",
            action="view",
            code="users:view",
            label="View Users",
        )
        admin_role = Role(name="Admin", permissions=[view_users])
        owner_role = Role(name="Franchise User")
        middelburg = Franchise(business_name="Middelburg", is_performance_active=False)
        admin = User(name="Admin", surname="User", email="admin@example.com", password_hash="x", roles=[admin_role])
        owner = User(name="Middelburg", surname="Owner", email="middelburg@example.com", password_hash="x", roles=[owner_role])
        db.session.add_all([middelburg, admin, owner])
        db.session.flush()
        ensure_own_primary_franchise_link(owner, middelburg)
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
            session["_fresh"] = True

        response = client.get("/admin/franchise-users")
        assert response.status_code == 200
        assert "middelburg@example.com" in response.get_data(as_text=True)
