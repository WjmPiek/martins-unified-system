"""Owner-only authorization applies beyond navigation visibility."""
import pytest
from flask import render_template
from flask_login import login_user
from test_native_territory import app, franchise, login, payload, HEADERS
from test_territory_research import request_data
from app.extensions import db
from app.models import User, ImportJob, TerritoryResearchRun
from app.franchise.territory_access import is_territory_owner
from app.franchise.territory_research import start, run_public_research
from app.franchise import territory_sources


@pytest.mark.parametrize("role", ["Admin", "Super Admin", "Finance Manager", "Franchise", "Employee", "Staff", "Manager"])
def test_non_owner_cannot_access_any_territory_endpoint_or_sidebar(app, franchise, role):
    client = login(app, franchise, role=role)
    user = User.query.one()
    user.email = "someone@martinsdirect.com"
    # A protected administrator display name must never bypass the email gate.
    user.name, user.surname = "Wjm", "Piek"
    db.session.commit()
    base = f"/franchise/{franchise.id}/territory"
    for path in ["/franchise/territories", base, base+"/report", base+"/export", base+"/research/1"]:
        assert client.get(path).status_code == 403, path
    for suffix in ["calculate", "save", "score-recommendations", "franchise-figures", "boundaries", "compose", "research"]:
        assert client.post(base+"/"+suffix, json=payload(), headers=HEADERS).status_code == 403, suffix
    with app.test_request_context():
        login_user(user)
        html = render_template("base.html")
        assert "/franchise/territories" not in html
    with pytest.raises(ValueError, match="restricted"):
        start(franchise.id, user.id, request_data())
    assert TerritoryResearchRun.query.count() == 0


def test_owner_sidebar_and_native_franchise_selector(app, franchise):
    client = login(app, franchise)
    response = client.get("/franchise/territories")
    assert response.status_code == 200
    assert b'href="/franchise/territories"' in response.data
    assert f'/franchise/{franchise.id}/territory'.encode() in response.data
    assert client.get(f"/franchise/{franchise.id}/territory").status_code == 200


@pytest.mark.parametrize("change", ["email", "active"])
def test_queued_jobs_recheck_owner_identity(app, franchise, monkeypatch, change):
    login(app, franchise)
    user = User.query.one()
    run = start(franchise.id, user.id, request_data())
    job = db.session.get(ImportJob, run.job_id)
    if change == "email":
        user.email = "other@martinsdirect.com"
    else:
        user.is_active_account = False
    db.session.commit()
    calls = []
    monkeypatch.setattr(territory_sources, "research", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError, match="revoked"):
        run_public_research(job)
    assert run.status == "cancelled" and not calls


def test_email_comparison_is_exact_and_case_insensitive(app, franchise):
    login(app, franchise)
    user = User.query.one()
    user.email = "WJM@MartinsDirect.com"
    assert is_territory_owner(user)
    user.email = "wjm@martinsdirect.com.other.example"
    assert not is_territory_owner(user)
    user.email = "wjm+other@martinsdirect.com"
    assert not is_territory_owner(user)


def test_running_research_rechecks_owner_before_persisting_result(app,franchise,monkeypatch):
    login(app,franchise)
    user=User.query.one()
    run=start(franchise.id,user.id,request_data())
    job=db.session.get(ImportJob,run.job_id)
    def research(**kwargs):
        user.email='other@martinsdirect.com'
        db.session.commit()
        return {'records':[]}
    monkeypatch.setattr(territory_sources,'research',research)
    result=run_public_research(job)
    assert result['status']=='cancelled'
    assert run.status=='cancelled' and job.status=='cancelled'
    assert not run.result_json


def test_private_territory_pages_do_not_allow_shared_or_browser_cache(app,franchise):
    client=login(app,franchise)
    for url in ['/franchise/territories',f'/franchise/{franchise.id}/territory']:
        assert client.get(url).headers['Cache-Control']=='no-store'
