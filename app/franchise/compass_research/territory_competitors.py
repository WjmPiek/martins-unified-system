"""Public operator discovery with source coordinates and explicit coverage limits."""
import concurrent.futures,html,json,re,time,threading,urllib.parse,urllib.request
from pathlib import Path
from . import business_lookup as base
from .public_web import safe_url,SafeRedirect
if not hasattr(base,'_geo_cache'):base._geo_cache={};base._geo_lock=threading.Lock();base._geo_last=0
def map_sources(lat,lon,radius):
 query=f'[out:json][timeout:8];(nwr(around:{radius},{lat},{lon})[shop=funeral_directors];nwr(around:{radius},{lat},{lon})[amenity=funeral_hall];nwr(around:{radius},{lat},{lon})[amenity=crematorium];nwr(around:{radius},{lat},{lon})[name~"funeral|begraf|undertak",i];);out center tags;'
 for host in ['https://overpass-api.de/api/interpreter','https://overpass.private.coffee/api/interpreter']:
  try:
   data=base.read_json(host+'?'+urllib.parse.urlencode({'data':query}));rows=[]
   for item in data.get('elements',[]):
    tags=item.get('tags',{});website=tags.get('website',tags.get('contact:website',''))
    if website.startswith('http://'):website='https://'+website[7:]
    rows.append({'id':f"osm-{item['type']}-{item['id']}",'name':tags.get('name','Unnamed funeral business'),'lat':item.get('lat',item.get('center',{}).get('lat')),'lon':item.get('lon',item.get('center',{}).get('lon')),'website':website,'phone':tags.get('phone',tags.get('contact:phone','')),'address':' '.join(tags.get(k,'')for k in ['addr:housenumber','addr:street','addr:suburb','addr:city']).strip(),'source':f"https://www.openstreetmap.org/{item['type']}/{item['id']}"})
   return rows
  except Exception:continue
 raise ValueError('OpenStreetMap servers unavailable')
def geocode(address):
 key=address.casefold()
 with base._geo_lock:
  if key in base._geo_cache:return base._geo_cache[key]
  time.sleep(max(0,1.05-(time.monotonic()-base._geo_last)));base._geo_last=time.monotonic()
  url='https://nominatim.openstreetmap.org/search?'+urllib.parse.urlencode({'q':address+', South Africa','countrycodes':'za','format':'jsonv2','addressdetails':1,'limit':2})
  rows=base.read_json(url);result=None
  for row in rows:
   # Reject a town/street-centre match: it is not evidence of premises.
   a=row.get('address',{});kind=row.get('addresstype',row.get('type',''))
   if a.get('country_code')=='za' and (a.get('house_number')or kind in ('house','building','commercial','funeral_directors','crematorium')):
    result={'lat':float(row['lat']),'lon':float(row['lon']),'locationNote':'Address matched to a public map pin; confirm current premises.','geocodeSource':'https://www.openstreetmap.org/'+row.get('osm_type','node')+'/'+str(row.get('osm_id',''))};break
  if len(base._geo_cache)>300:base._geo_cache.clear()
  base._geo_cache[key]=result;return result
def website_branches(source):
 url=source['url'];safe_url(url);req=urllib.request.Request(url,headers={'User-Agent':'FranchiseCompassSA/1.0 public branch research'})
 with urllib.request.build_opener(SafeRedirect()).open(req,timeout=5)as response:
  if 'html' not in response.headers.get('Content-Type',''):return[]
  final=response.geturl();safe_url(final);text=response.read(1500000).decode(errors='replace')
 found=[]
 def walk(value):
  if isinstance(value,list):
   for item in value:walk(item)
  elif isinstance(value,dict):
   address=value.get('address');geo=value.get('geo',{});name=value.get('name')
   if name and isinstance(address,dict) and address.get('streetAddress'):
    street=', '.join(str(address.get(k,''))for k in ['streetAddress','addressLocality','addressRegion','postalCode']if address.get(k))
    row={'id':'web-branch-'+final+'-'+str(name),'name':html.unescape(str(name))[:150],'address':street,'phone':str(value.get('telephone',''))[:80],'website':final,'source':final,'locationNote':'Published business address; pin unconfirmed.'}
    try:
     lat=float(geo.get('latitude'));lon=float(geo.get('longitude'))
     if -35.3<=lat<=-22 and 16<=lon<=34:row.update(lat=lat,lon=lon,locationNote='Coordinates published in business website data; confirm current premises.')
    except (ValueError,TypeError,AttributeError):pass
    if re.search(r'funer|cremat|undertak|begraf',str(name)+' '+final,re.I):found.append(row)
   for key,item in value.items():
    if key not in ('address','geo'):walk(item)
 for raw in re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>',text,re.S|re.I):
  try:walk(json.loads(html.unescape(raw)))
  except (ValueError,TypeError):continue
 return found[:8]
def lookup(context):
 lat=float(context.get('lat'));lon=float(context.get('lon'));radius=float(context.get('radius',15000))
 if not(-35.3<=lat<=-22 and 16<=lon<=34 and 500<=radius<=30000):raise ValueError('Invalid selected-area directory request')
 from .web_price_search import discover
 rows=[];warnings=[];sources=[];web=[]
 jobs=[('Official branch locator',lambda:base.official_branches(lat,lon,radius)),('OpenStreetMap directory',lambda:map_sources(lat,lon,radius)),('Area web discovery',lambda:discover({k:v for k,v in context.items()if k!='mode'}))]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
  futures={pool.submit(fn):name for name,fn in jobs}
  for future in concurrent.futures.as_completed(futures):
   name=futures[future]
   try:
    value=future.result();sources.append(name)
    if name=='Area web discovery':web=value.get('sources',[]);warnings.extend(value.get('errors',[]))
    else:rows.extend(value)
   except Exception:warnings.append(name+' unavailable; coverage is incomplete.')
 web=sorted(web,key=lambda s:not s.get('localMention'))[:4]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
  futures=[pool.submit(website_branches,s)for s in web]
  for future in concurrent.futures.as_completed(futures):
   try:rows.extend(future.result())
   except Exception:pass
 for source in web:
  if source.get('localMention') and not any(row.get('source')==source['url']for row in rows) and not re.search(r'funeralcosts|funeralza|safunerals|procompare|wikipedia',source['url'],re.I):
   rows.append({'id':'web-lead-'+source['url'],'name':source['title'],'address':'Public web listing — premises and exact location need confirmation','website':source['url'],'source':source['url'],'areaAnchor':[lon,lat],'locationNote':'The page mentions a searched town/suburb. This is an unlocated lead, not proof that a branch lies within the boundary.'})
 seeds=[s for s in context.get('seeds',[])[:8]if isinstance(s,dict)and isinstance(s.get('name'),str)and isinstance(s.get('address'),str)]
 rows.extend({**s,'id':s.get('id','web-seed-'+s['name'])}for s in seeds)
 pending=[row for row in rows if row.get('lat')is None and re.search(r'\b\d+\b',row.get('address',''))][:4]
 for row in pending:
  try:
   pin=geocode(row['address'])
   if pin:row.update(pin)
  except Exception:pass
 warnings.append('Public directories and indexed websites cannot establish that every operating funeral parlour is listed. Confirm missing or recently opened premises locally.')
 return{'competitors':rows,'source':'; '.join(sources),'warnings':warnings,'status':'partial' if sources else 'unavailable'}
