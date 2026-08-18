from datetime import date
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models import Franchise, MonthlyFigure, RoyaltyScale
from app.royalties.routes import build_individual_royalty_row
from app.royalty_engine import calculate_monthly_figure


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


def _germiston_june_figure(franchise):
    return MonthlyFigure(
        franchise=franchise,
        month=6,
        year=2026,
        funeral_receipts=Decimal("10000"),
        insurance_receipts=Decimal("1000"),
        insurance_payover=Decimal("3000"),
    )


def test_negative_insurance_difference_reduces_new_method_royalty_base():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(
            business_name="Germiston",
            agreement_start_date=date(2020, 1, 1),
            minimum_royalty_is_none=True,
        )
        db.session.add(franchise)
        db.session.flush()
        db.session.add(RoyaltyScale(
            franchise_id=franchise.id,
            row_number=1,
            amount_from=Decimal("0"),
            amount_to=Decimal("999999"),
            percentage=Decimal("5"),
        ))
        figure = _germiston_june_figure(franchise)
        db.session.add(figure)
        db.session.commit()

        result = calculate_monthly_figure(figure)

        assert figure.admin_fee == Decimal("-2000")
        assert result.royalty_base == Decimal("8000")
        assert result.royalty_percentage == Decimal("5")
        assert result.royalty_amount == Decimal("400")

        display_row = build_individual_royalty_row(figure)
        assert display_row.admin_fee == Decimal("-2000")
        assert display_row.gross_turnover == Decimal("8000")
        assert display_row.royalty_amount == Decimal("400")


def test_negative_insurance_difference_cannot_create_negative_royalty():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(
            business_name="Germiston",
            agreement_start_date=date(2020, 1, 1),
            minimum_royalty_is_none=True,
        )
        db.session.add(franchise)
        db.session.flush()
        db.session.add(RoyaltyScale(
            franchise_id=franchise.id,
            row_number=1,
            amount_from=Decimal("0"),
            amount_to=Decimal("999999"),
            percentage=Decimal("5"),
        ))
        figure = MonthlyFigure(
            franchise=franchise,
            month=6,
            year=2026,
            funeral_receipts=Decimal("1000"),
            insurance_receipts=Decimal("1000"),
            insurance_payover=Decimal("5000"),
        )
        db.session.add(figure)
        db.session.commit()

        result = calculate_monthly_figure(figure)

        assert figure.admin_fee == Decimal("-4000")
        assert result.royalty_base == Decimal("0")
        assert result.royalty_amount == Decimal("0")
