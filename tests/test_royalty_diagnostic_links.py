from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import render_template_string

from test_native_territory import app


class DiagnosticLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'royalty-diagnostic-link' in attrs.get('class', '').split():
            self.links.append(attrs)


@pytest.mark.parametrize('message,franchise_id,anchor', [
    ('missing agreement start date', 7, 'agreement-card'),
    ('imported period is before agreement start date', 7, 'agreement-card'),
    ('imported period is after agreement end date', 7, 'agreement-card'),
    ('could not validate imported period against agreement dates', 7, 'agreement-card'),
    ('missing royalty scale', 7, 'royalty-scale-card'),
    ('overlapping royalty brackets; highest threshold used', 17, 'royalty-scale-card'),
    ('base above configured brackets; final bracket used', 17, 'royalty-scale-card'),
    ('no royalty bracket matched', 17, 'royalty-scale-card'),
    ('royalty base has value but royalty percentage is 0', 17, 'royalty-scale-card'),
    ('legacy diagnostic requiring review', 7, ''),
])
def test_diagnostic_opens_the_relevant_record_and_section(app, message, franchise_id, anchor):
    snapshot = SimpleNamespace(franchise_id=7, franchise=SimpleNamespace(business_name='Branch Seven'),
                               scale_source_franchise_id=17, scale_source_franchise_name='Scale Source')
    with app.test_request_context():
        html = render_template_string('{% from "admin/_royalty_diagnostic_links.html" import diagnostic_link %}{{ diagnostic_link(message, snapshot) }}', message=message, snapshot=snapshot)
    parser = DiagnosticLinks(); parser.feed(html)
    assert len(parser.links) == 1
    target = urlsplit(parser.links[0]['href'])
    assert target.path == '/franchise/details'
    assert parse_qs(target.query) == {'franchise_id': [str(franchise_id)]}
    assert target.fragment == anchor


def test_deleted_franchise_opens_integrity_review_and_messages_are_escaped(app):
    snapshot = SimpleNamespace(franchise_id=7, franchise=None)
    with app.test_request_context():
        html = render_template_string('{% from "admin/_royalty_diagnostic_links.html" import diagnostic_link %}{{ diagnostic_link(message, snapshot) }}', message='<script>bad()</script>', snapshot=snapshot)
    parser = DiagnosticLinks(); parser.feed(html)
    assert urlsplit(parser.links[0]['href']).path in {'/admin/franchise-master', '/admin/data-integrity'}
    assert '<script>' not in html and '&lt;script&gt;' in html


def test_snapshot_errors_open_the_correct_franchise_editor(app):
    from app.extensions import db
    from app.models import MonthlyFigure
    from app.royalty_management import snapshot_monthly_figure
    from test_access_profiles import setup_accounts, sign_in, call

    _, _, branches, _ = setup_accounts(app, office_role='Admin')
    figure = MonthlyFigure(franchise_id=branches[0].id, month=10, year=2026, funeral_receipts=1000)
    db.session.add(figure); db.session.flush()
    snapshot_monthly_figure(figure); db.session.commit()
    client = app.test_client(); sign_in(client, 'head_office')
    response = call(client, 'get', '/admin/royalty-management?section=snapshots&month=10&year=2026')
    assert response.status_code == 200
    parser = DiagnosticLinks(); parser.feed(response.text)
    assert len(parser.links) >= 2
    for link in parser.links:
        target = urlsplit(link['href'])
        assert parse_qs(target.query)['franchise_id'] == [str(branches[0].id)]
        editor = call(client, 'get', target.path + '?' + target.query)
        assert editor.status_code == 200
        assert f'id="{target.fragment}"' in editor.text
        assert f'name="franchise_id" value="{branches[0].id}"' in editor.text
