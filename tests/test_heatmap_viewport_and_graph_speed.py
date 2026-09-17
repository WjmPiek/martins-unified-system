from decimal import Decimal

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    Franchise, HeatmapRecord, PerformanceResult, Permission, Role, User,
    UserModuleAccess, user_franchises,
)
from app.performance.service import _bulk_graph_payload


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_heatmap_table_and_viewport_are_hard_bounded():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        permission = Permission(module="Heat Map", action="view", code="heat_map:view", label="View")
        role = Role(name="Franchise User", permissions=[permission])
        franchise = Franchise(business_name="Large Franchise")
        user = User(
            name="Large", surname="User", email="large@example.com", password_hash="x",
            roles=[role], assigned_franchises=[franchise],
        )
        db.session.add_all([franchise, user])
        db.session.flush()
        db.session.execute(
            user_franchises.update()
            .where(user_franchises.c.user_id == user.id)
            .where(user_franchises.c.franchise_id == franchise.id)
            .values(is_primary=True)
        )
        db.session.add(UserModuleAccess(user_id=user.id, module_code="heat_map:view", is_enabled=True))
        db.session.bulk_save_objects([
            HeatmapRecord(
                franchise_id=franchise.id,
                mf_file=f"MF-{index}",
                deceased_name="Central Church" if index < 2 else "Client",
                city="Johannesburg",
                province="Gauteng",
                full_address="1 Central Road, Johannesburg" if index < 2 else "",
                relation="MAP:church" if index < 2 else "MAP:insurance_clients",
                latitude=-26.20 + (index % 20) * 0.0001,
                longitude=28.04 + (index % 20) * 0.0001,
            )
            for index in range(1601)
        ])
        db.session.commit()

        client = app.test_client()
        _login(client, user.id)
        table = client.get(f"/heat-map/data?franchise_id={franchise.id}&per_page=5000").get_json()
        assert table["summary"]["total"] == 1601
        assert len(table["records"]) == 500
        assert table["pagination"]["hasMore"] is True

        detail_response = client.get(
            f"/heat-map/viewport?franchise_id={franchise.id}&zoom=13"
            "&south=-27&north=-25&west=27&east=29"
        )
        assert detail_response.status_code == 200
        detail = detail_response.get_json()
        assert detail["mode"] == "detail"
        assert len(detail["points"]) == 1500
        assert detail["truncated"] is True

        aggregate = client.get(
            "/heat-map/viewport?zoom=5&south=-27&north=-25&west=27&east=29"
        ).get_json()
        assert aggregate["mode"] == "aggregate"
        assert len(aggregate["points"]) < 100
        assert sum(point["count"] for point in aggregate["points"]) == 1601

        venue_groups = client.get(
            f"/heat-map/venue-groups?franchise_id={franchise.id}&record_type=church"
        ).get_json()["groups"]
        assert len(venue_groups) == 1
        assert venue_groups[0]["services"] == 2


def test_graph_cache_miss_uses_one_performance_result_query():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(business_name="Fast Graph Franchise")
        db.session.add(franchise)
        db.session.flush()
        rows = []
        for year in (2024, 2025):
            for month in range(1, 13):
                rows.append(PerformanceResult(
                    franchise_id=franchise.id,
                    metric="cash",
                    year=year,
                    month=month,
                    actual_value=Decimal(year * 100 + month),
                    target_value=Decimal(year * 100 + month + 10),
                    achievement_percent=Decimal("99"),
                    growth_percent=Decimal("1"),
                    previous_month_value=Decimal("0"),
                    same_month_last_year_value=Decimal("0"),
                    three_year_average_value=Decimal("0"),
                    forecast_value=Decimal("0"),
                ))
        db.session.add_all(rows)
        db.session.commit()

        statements = []

        def track(_connection, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().lower().startswith("select") and "performance_results" in statement.lower():
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", track)
        try:
            payload = _bulk_graph_payload([franchise.id], "cash", 12, 2025, 12)
        finally:
            event.remove(db.engine, "before_cursor_execute", track)

        assert payload["cache_status"] == "rebuilt"
        assert len(payload["actual_vs_target"]) == 12
        assert len(payload["rolling_12"]) == 12
        assert len(statements) == 1
