from app import create_app
from app.extensions import db
from app.models import Permission, Role, User, UserModuleAccess, ensure_mandatory_franchise_modules


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


def test_manuals_cannot_be_disabled_and_other_optional_modules_need_admin_activation():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        manuals = Permission(module="Manuals", action="view", code="manuals:view", label="View Manuals")
        heat_map = Permission(module="Heat Map", action="view", code="heat_map:view", label="View Heat Map")
        role = Role(name="Franchise User", permissions=[manuals, heat_map])
        owner = User(
            name="Existing",
            surname="Owner",
            email="existing@example.com",
            password_hash="x",
            roles=[role],
        )
        db.session.add(owner)
        db.session.flush()
        db.session.add(UserModuleAccess(
            user_id=owner.id,
            module_code="manuals:view",
            is_enabled=False,
        ))
        db.session.commit()

        # A legacy disabled row cannot remove the compulsory module.
        assert owner.has_permission("manuals:view") is True
        # Role permissions alone do not activate optional modules.
        assert owner.has_permission("heat_map:view") is False

        assert ensure_mandatory_franchise_modules(owner) == 1
        db.session.flush()
        assert UserModuleAccess.query.filter_by(
            user_id=owner.id, module_code="manuals:view"
        ).one().is_enabled is True

        db.session.add(UserModuleAccess(
            user_id=owner.id,
            module_code="heat_map:view",
            is_enabled=True,
        ))
        db.session.commit()
        assert owner.has_permission("heat_map:view") is True


def test_migration_backfills_existing_franchise_users():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from migrations.versions import v123_mandatory_franchise_manuals as migration

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        role = Role(name="Franchise User")
        owner = User(
            name="Legacy",
            surname="Owner",
            email="legacy@example.com",
            password_hash="x",
            roles=[role],
        )
        db.session.add(owner)
        db.session.commit()
        owner_id = owner.id

        with db.engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()

        db.session.expire_all()
        access = UserModuleAccess.query.filter_by(
            user_id=owner_id, module_code="manuals:view"
        ).one()
        assert access.is_enabled is True
