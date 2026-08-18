from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import Permission, Role, User


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


def test_performance_insights_renders_dictionary_items_list():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        permission = Permission(
            module="Performance",
            action="view",
            code="performance:view",
            label="View Performance",
        )
        role = Role(name="Admin", permissions=[permission])
        admin = User(
            name="Admin",
            surname="User",
            email="admin@example.com",
            password_hash="x",
            roles=[role],
        )
        db.session.add(admin)
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
            session["_fresh"] = True

        insight_data = {
            "period_label": "June 2026",
            "franchise": None,
            "counts": {},
            "items": [
                {
                    "severity": "warning",
                    "title": "Review branch",
                    "message": "Performance needs attention.",
                    "action": "Review the monthly figures.",
                    "franchise_id": None,
                }
            ],
        }
        with (
            patch("app.performance.routes.accessible_franchise_ids", return_value=[]),
            patch("app.performance.routes.ensure_performance_results"),
            patch("app.performance.routes.get_selected_franchise", return_value=None),
            patch("app.performance.routes.executive_insights", return_value=insight_data),
            patch("app.performance.routes.reporting_years", return_value=[2026]),
        ):
            response = client.get("/performance/insights?month=6&year=2026")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Performance Insights" in html
        assert "June 2026" in html
        assert "Review branch" in html
