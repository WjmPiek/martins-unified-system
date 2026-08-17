from decimal import Decimal

from app import create_app
from app.extensions import db
from app.franchise.routes import missing_royalty_setup_items
from app.models import Franchise, RoyaltyScale
from app.royalty_engine import calculate_royalty_amount


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


def test_explicit_none_uses_scale_amount_without_minimum():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(
            business_name="Scale Only Franchise",
            minimum_royalty_amount=Decimal("1000"),
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
        db.session.commit()

        percentage, amount, minimum, applied, *_rest = calculate_royalty_amount(
            franchise, Decimal("10000")
        )

        assert percentage == Decimal("5")
        assert amount == Decimal("500")
        assert minimum == Decimal("0")
        assert applied is False
        assert "minimum royalty" not in missing_royalty_setup_items(franchise)


def test_amount_mode_still_applies_configured_minimum():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        franchise = Franchise(
            business_name="Minimum Franchise",
            minimum_royalty_amount=Decimal("1000"),
            minimum_royalty_is_none=False,
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
        db.session.commit()

        _percentage, amount, minimum, applied, *_rest = calculate_royalty_amount(
            franchise, Decimal("10000")
        )

        assert amount == Decimal("1000")
        assert minimum == Decimal("1000")
        assert applied is True
