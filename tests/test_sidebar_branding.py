from html.parser import HTMLParser
import pytest
from test_native_territory import app
from test_access_profiles import setup_accounts, sign_in, call

class SidebarParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.stack=[]; self.brand_locations=[]; self.brand_urls=[]; self.menu_urls=[]; self.body_classes=set()
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs); classes=set(attrs.get('class','').split())
        in_navigation=any('sidebar-nav' in entry[1] for entry in self.stack)
        if tag=='body': self.body_classes=classes
        if tag=='a':
            if 'ms-brand' in classes:
                self.brand_locations.append(in_navigation)
                self.brand_urls.append(attrs.get('href',''))
            elif in_navigation:
                self.menu_urls.append(attrs.get('href',''))
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.stack.append((tag,classes))
    def handle_endtag(self,tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                self.stack=self.stack[:index];break

@pytest.mark.parametrize('profile_mode',['main','franchise'])
def test_brand_is_not_a_menu_item_on_the_graph_page(app,profile_mode):
    _, _, branches, profile=setup_accounts(app,office_role='Admin')
    client=app.test_client()
    sign_in(client,'head_office' if profile_mode=='main' else f'branch:{profile.id}')
    response=call(client,'get','/performance/graphs')
    assert response.status_code==200
    page=SidebarParser(); page.feed(response.text)
    assert page.brand_locations==[False]
    assert any(url.startswith('/performance/graphs') for url in page.menu_urls)
    assert page.brand_urls==['/performance/graphs']
    assert ('franchise-user-portal' in page.body_classes)==(profile_mode=='franchise')
