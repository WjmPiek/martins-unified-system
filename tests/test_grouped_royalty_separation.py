from decimal import Decimal

from app.models import Franchise, MonthlyFigure
from app.royalties import routes


def _monthly_figure(franchise, receipts, persisted_percentage, persisted_amount):
    return MonthlyFigure(
        id=franchise.id,
        franchise=franchise,
        franchise_id=franchise.id,
        month=7,
        year=2026,
        status="Published",
        funeral_receipts=Decimal(receipts),
        society_receipts=Decimal("0"),
        cash_sales=Decimal("0"),
        tombstone_receipts=Decimal("0"),
        obo_service_receipts=Decimal("0"),
        insurance_receipts=Decimal("0"),
        insurance_payover=Decimal("0"),
        royalty_percentage=Decimal(persisted_percentage),
        royalty_amount=Decimal(persisted_amount),
    )


def test_individual_rows_stay_separate_from_main_scale_group_total(monkeypatch):
    main = Franchise(id=1, business_name="Main Franchise")
    linked = Franchise(id=2, business_name="Linked Franchise")
    main_row = _monthly_figure(main, "100", "99", "999")
    linked_row = _monthly_figure(linked, "200", "0", "0")

    monkeypatch.setattr(
        routes,
        "calculate_royalty_base",
        lambda row, franchise: (Decimal(row.funeral_receipts), "old"),
    )
    monkeypatch.setattr(
        routes,
        "calculate_royalty",
        lambda franchise, base: (
            Decimal(base),
            Decimal(franchise.id),
            Decimal(base) * Decimal(franchise.id) / Decimal("100"),
            False,
        ),
    )

    individual_main = routes.build_individual_royalty_row(main_row)
    individual_linked = routes.build_individual_royalty_row(linked_row)
    grouped = routes.build_grouped_royalty_row(
        [individual_main, individual_linked], main, 7, 2026
    )

    assert individual_main.royalty_percentage == Decimal("1")
    assert individual_main.royalty_amount == Decimal("1")
    assert individual_linked.royalty_percentage == Decimal("2")
    assert individual_linked.royalty_amount == Decimal("4")
    assert grouped.gross_revenue == Decimal("300")
    assert grouped.royalty_percentage == Decimal("1")
    assert grouped.royalty_amount == Decimal("3")
    assert grouped.royalty_amount != individual_main.royalty_amount
