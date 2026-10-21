from copy import deepcopy
from datetime import date
from decimal import Decimal
import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, inspect
from app import create_app
from app.extensions import db
from app.models import (Franchise, FranchiseTerritory, TerritoryValuationSnapshot,
    RoyaltyScale, MonthlyFigure, Role, Permission, User)
from app.franchise.territory_finance import DEFAULTS, WEIGHTS, forecast, calculate, scorecard, validate_inputs
from app.franchise.territory_routes import validate_geometry, validate_payload


class Config:
    SECRET_KEY = "territory-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TERRITORY_VALUATION_ENABLED = True
    TESTING = True


@pytest.fixture
def app():
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def franchise(app):
    f = Franchise(business_name="Test franchise", province="Gauteng", agreement_start_date=date(2020, 1, 1),
                  agreement_end_date=date(2040, 1, 1), minimum_royalty_is_none=True)
    db.session.add(f)
    db.session.flush()
    db.session.add(RoyaltyScale(franchise_id=f.id, row_number=1, amount_from=0,
        amount_to=Decimal("999999999999"), percentage=6))
    db.session.commit()
    return f


def inputs(**changes):
    return {**DEFAULTS, "population": 120000, "mortality_per_1000": 10,
        "volume_growth": 0, "price_growth": 0, "cost_growth": 0,
        "reserve": 0, "ramp_months": 0, "target_margin": 20, "confirmed": True, **changes}


def payload():
    return {"inputs": inputs(), "scores": {k: 3 for k in WEIGHTS},
            "score_evidence": {k: "Reviewed source and local validation" for k in WEIGHTS}}


def login(app, franchise, role="Admin", permissions=("view", "edit", "export")):
    codes = [Permission(module="Franchise Details", action=a, code=f"franchise_details:{a}", label=a) for a in permissions]
    r = Role(name=role, permissions=codes)
    u = User(name="Test", surname="User", email="wjm@martinsdirect.com", password_hash="x", roles=[r], assigned_franchises=[franchise])
    db.session.add(u)
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True
        s["territory_csrf"] = "test-token"
    return client


HEADERS = {"X-Territory-CSRF": "test-token"}


def test_forecast_hand_calculated_and_no_operational_writes(franchise):
    result = forecast(franchise, inputs())
    first = result["months"][0]
    assert first["services"] == 10
    assert first["revenue"] == 200000
    assert first["royalty"] == 12000
    assert first["operating"] == 43000
    assert first["repayment"] == 2500
    assert first["retained"] == 40500
    assert result["ending_cash"] == 1950000
    assert len(result["months"]) == 60 and len(result["years"]) == 5
    assert MonthlyFigure.query.count() == 0


def test_score_weights_and_original_fee_bands(franchise):
    for score, fee in [(0, 75000), (2, 112500), (3, 150000), (4, 187500), (5, 225000)]:
        card = scorecard({k: score for k in WEIGHTS}, {k: "Evidence" for k in WEIGHTS})
        assert card["score_fee"] == fee
    assert not scorecard({}, {})["complete"]
    result = calculate(franchise, inputs(), {k: 3 for k in WEIGHTS}, {k: "Evidence" for k in WEIGHTS})
    assert result["recommended_fee"] == 150000
    assert result["downside"]["ending_cash"] == 714000


def test_missing_confirmation_and_unprofitable_case_never_recommend(franchise):
    scores = {k: 3 for k in WEIGHTS}
    evidence = {k: "Evidence" for k in WEIGHTS}
    assert calculate(franchise, inputs(confirmed=False), scores, evidence)["recommended_fee"] is None
    assert calculate(franchise, inputs(market_share=0), scores, evidence)["recommended_fee"] is None


@pytest.mark.parametrize("term", [1, 7, 36, 60])
def test_repayments_reconcile_to_cent(franchise, term):
    result = forecast(franchise, inputs(territory_fee=112500.01, repayment_months=term))
    assert sum(r["repayment"] for r in result["months"]) == Decimal("112500.01")
    assert result["months"][-1]["principal_balance"] == 0


def test_interest_and_upfront_cash_flows(franchise):
    result = forecast(franchise, inputs(interest_percent=12))
    assert sum(r["repayment"] - r["interest"] for r in result["months"]) == 150000
    upfront = forecast(franchise, inputs(payment_mode="upfront"))
    assert upfront["initial"] == 630000
    assert all(r["repayment"] == 0 for r in upfront["months"])
    assert upfront["ending_cash"] == forecast(franchise, inputs())["ending_cash"]


def test_monthly_cents_reconcile_with_annual_and_total_cash(franchise):
    result = forecast(franchise, inputs(volume_growth=2, price_growth=4, cost_growth=5, tax_percent=27,
        reserve=5000, annual_capex=11111.11, territory_fee=112500.01, interest_percent=10.5))
    assert sum(y["retained"] for y in result["years"]) - result["initial"] == result["ending_cash"]
    for row in result["months"]:
        assert row["revenue"] - row["direct"] - row["royalty"] - row["overhead"] == row["operating"]
        assert row["operating"] - row["tax"] - row["cash_deductions"] - row["repayment"] == row["retained"]


def test_contract_method_minimum_none_and_negative_admin(franchise):
    new = forecast(franchise, inputs(policy_receipts=5000, policy_payover=7000))
    assert new["months"][0]["royalty"] == 11880
    franchise.agreement_start_date = date(2017, 1, 1)
    old = forecast(franchise, inputs(policy_receipts=5000, policy_payover=7000))
    assert old["months"][0]["royalty"] == 12300
    franchise.minimum_royalty_is_none = False
    franchise.minimum_royalty_amount = 15000
    assert forecast(franchise, inputs())["months"][0]["royalty"] == 15000


def test_monthly_funding_deficit_cannot_pass_on_later_profits(franchise):
    result = forecast(franchise, inputs(working_capital=0, ramp_months=12))
    assert result["ending_cash"] > 0
    assert result["additional_funding"] > 0
    assert not result["affordable"]


def test_missing_royalty_setup_blocks_recommendation(franchise):
    franchise.agreement_start_date = None
    result = calculate(franchise, inputs(), {k: 3 for k in WEIGHTS}, {k: "Evidence" for k in WEIGHTS})
    assert "missing agreement start date" in result["expected"]["errors"]
    assert result["recommended_fee"] is None


def test_price_search_reuses_royalty_queries_and_snapshots_contract(franchise):
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        if "royalty_scales" in statement and statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(db.engine, "before_cursor_execute", record)
    try:
        result = calculate(franchise, inputs(volume_growth=2, price_growth=4, cost_growth=5),
            {k: 3 for k in WEIGHTS}, {k: "Evidence" for k in WEIGHTS})
        assert len(statements) < 30
        assert result["royalty_policy"]["scales"][0]["percent"] == 6
    finally:
        event.remove(db.engine, "before_cursor_execute", record)


@pytest.mark.parametrize("changes", [{"population": "NaN"}, {"fixed_cost": float("inf")},
    {"population": True}, {"repayment_months": 1.5}, {"market_share": 101},
    {"mortality_per_1000": 51}, {"household_size": 0}, {"confirmed": "true"}, {"volume_growth": -51}])
def test_untrusted_numeric_inputs(changes):
    with pytest.raises(ValueError):
        validate_inputs(inputs(**changes))


def test_geometry_rejects_crossings_nan_and_foreign_coordinates():
    good = {"type": "Polygon", "coordinates": [[[27, -26], [28, -26], [28, -27], [27, -26]]]}
    assert validate_geometry(good) == good
    for ring in [[[27, -26], [28, -27], [27, -27], [28, -26], [27, -26]],
                 [[float("nan"), -26], [28, -26], [28, -27], [float("nan"), -26]],
                 [[0, 0], [1, 0], [1, 1], [0, 0]]]:
        with pytest.raises(ValueError):
            validate_geometry({"type": "Polygon", "coordinates": [ring]})


def test_native_page_save_conflict_export_and_unchanged_source_records(app, franchise):
    client = login(app, franchise)
    url = f"/franchise/{franchise.id}/territory"
    assert client.get(url).status_code == 200
    assert b"Territory &amp; Valuation" in client.get(url).data
    calculated = client.post(url + "/calculate", json=payload(), headers=HEADERS)
    assert calculated.status_code == 200
    assert calculated.json["recommended_fee"] is None  # No sourced boundary/evidence yet.
    assert client.post(url + "/save", json={"version": 0, "payload": payload()}, headers=HEADERS).json == {"version": 1}
    assert client.post(url + "/save", json={"version": 0, "payload": payload()}, headers=HEADERS).status_code == 409
    assert client.post(url + "/save", json={"version": 1, "payload": payload()}, headers=HEADERS).json == {"version": 2}
    assert TerritoryValuationSnapshot.query.count() == 2
    export = client.get(url + "/export?version=1")
    assert export.status_code == 200 and export.json["version"] == 1
    assert export.json["result"]["status"] == "draft"
    assert MonthlyFigure.query.count() == 0
    assert Franchise.query.count() == 1
    assert franchise.minimum_royalty_is_none


def test_permission_revocation_and_no_cross_franchise_access(app, franchise):
    client = login(app, franchise, role="Franchise User")
    other = Franchise(business_name="Other")
    db.session.add(other); db.session.commit()
    url = f"/franchise/{franchise.id}/territory"
    assert client.get(url).status_code == 200
    assert client.get(f"/franchise/{other.id}/territory").status_code == 403
    assert client.get(f"/franchise/{other.id}/territory/export").status_code == 403
    assert client.post(url + "/save", json={"version": 0, "payload": payload()}, headers=HEADERS).status_code == 403


def test_admin_does_not_bypass_revoked_permissions_or_csrf(app, franchise):
    client = login(app, franchise, permissions=("view",))
    url = f"/franchise/{franchise.id}/territory"
    assert client.post(url + "/calculate", json=payload(), headers=HEADERS).status_code == 403
    assert client.get(url + "/export").status_code == 403
    role = Role.query.filter_by(name="Admin").one()
    role.permissions.append(Permission(module="Franchise Details", action="edit", code="franchise_details:edit", label="Edit"))
    db.session.commit()
    assert client.post(url + "/calculate", json=payload()).status_code == 400
    app.config["TERRITORY_VALUATION_ENABLED"] = False
    assert client.get(url).status_code == 404


def test_source_validation_requires_dates_and_safe_link_scheme():
    item = payload()
    source = {"metric": "population", "url": "https://www.statssa.gov.za/", "data_date": "2022-01-01",
              "retrieved_at": "2026-01-01", "confidence": "medium", "method": "ward allocation", "value": "120000"}
    item["sources"] = [source]
    validate_payload(item)
    for changes in [{"url": "javascript:alert(1)"}, {"data_date": "unknown"}, {"retrieved_at": "2999-01-01"}, {"confidence": "certain"}]:
        candidate = deepcopy(item); candidate["sources"][0].update(changes)
        with pytest.raises(ValueError): validate_payload(candidate)


def test_migration_changes_only_new_tables(app, franchise):
    db.session.remove()
    TerritoryValuationSnapshot.__table__.drop(db.engine)
    FranchiseTerritory.__table__.drop(db.engine)
    path = Path(__file__).parents[1] / "migrations/versions/v128_native_territory.py"
    spec = importlib.util.spec_from_file_location("territory_migration", path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with db.engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert "franchise_territories" in inspect(connection).get_table_names()
        assert connection.exec_driver_sql("SELECT count(*) FROM franchises").scalar() == 1
        migration.downgrade()
        assert "franchise_territories" not in inspect(connection).get_table_names()
        assert connection.exec_driver_sql("SELECT count(*) FROM franchises").scalar() == 1
        migration.upgrade()


@pytest.mark.parametrize('changes', [{'scores':None},{'scores':[]},{'scores':{'population':True}},
    {'score_evidence':[]},{'score_evidence':{'unknown':'note'}},
    {'inputs':{'payment_mode':[]}},{'inputs':{'demand_basis':{}}},{'inputs':{'expense_mode':[]}}])
def test_malformed_scorecard_and_enum_inputs_return_validation_errors(app,franchise,changes):
    client=login(app,franchise)
    data=payload();data.update(changes)
    base=f'/franchise/{franchise.id}/territory'
    for path,body in [('/calculate',data),('/score-recommendations',data),('/save',{'version':0,'payload':data})]:
        response=client.post(base+path,json=body,headers=HEADERS)
        assert response.status_code==400
        assert response.json['error']
    assert TerritoryValuationSnapshot.query.count()==0
