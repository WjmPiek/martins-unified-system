from pathlib import Path


def test_royalty_summary_cards_are_above_filters_and_group_details():
    template = (
        Path(__file__).parents[1] / "app" / "templates" / "royalties" / "index.html"
    ).read_text(encoding="utf-8")

    summary_cards = template.index('<div class="royalty-dashboard-grid">')
    filters = template.index('<div class="filter-card branch-context-card">')
    grouped_details = template.index('<div class="form-section-card linked-dashboard-group">')

    assert summary_cards < filters < grouped_details
    assert template.count('<div class="royalty-dashboard-grid">') == 1
