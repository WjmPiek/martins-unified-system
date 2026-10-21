"""Independent public business sources, with explicit partial-coverage status."""
import concurrent.futures,html,json,re,urllib.request,urllib.parse

def read_json(url):
 request=urllib.request.Request(url,headers={'User-Agent':'FranchiseCompassSA/1.0 public branch research','Accept':'application/json'})
 with urllib.request.urlopen(request,timeout=8) as response:return json.load(response)

def official_branches(lat,lon,radius):
 endpoint='https://martinsfunerals.co.za/wp-admin/admin-ajax.php?'+urllib.parse.urlencode({'action':'store_search','lat':lat,'lng':lon,'max_results':50,'search_radius':radius/1000})
 result=read_json(endpoint)
 if not isinstance(result,list):raise ValueError('Invalid branch listing')
 rows=[]
 for item in result:
  source=item.get('permalink') or 'https://martinsfunerals.co.za/branch-locator/'
  rows.append({'id':'directory-martins-'+str(item.get('id','')),'name':html.unescape(item.get('store','Martin’s Funerals')),'address':html.unescape(', '.join(str(item[k]) for k in ['address','address2','city'] if item.get(k))),'lat':float(item['lat']),'lon':float(item['lng']),'phone':html.unescape(item.get('phone','')).replace('\ufffd',''),'source':source,'website':source,'locationNote':'Official branch-locator pin; confirm current premises. This source covers one brand.'})
 return rows

def map_branches(lat,lon,radius):
 query=f'[out:json][timeout:6];(nwr(around:{radius},{lat},{lon})[shop=funeral_directors];nwr(around:{radius},{lat},{lon})[amenity=funeral_hall];);out center tags;'
 result=read_json('https://overpass-api.de/api/interpreter?'+urllib.parse.urlencode({'data':query}));rows=[]
 for item in result.get('elements',[]):
  tags=item.get('tags',{});website=tags.get('website',tags.get('contact:website',''))
  if website.startswith('http://'):website='https://'+website[7:]
  rows.append({'id':f"osm-{item['type']}-{item['id']}",'name':tags.get('name','Unnamed funeral business'),'lat':item.get('lat',item.get('center',{}).get('lat')),'lon':item.get('lon',item.get('center',{}).get('lon')),'website':website,'phone':tags.get('phone',tags.get('contact:phone','')),'address':' '.join(tags.get(k,'') for k in ['addr:housenumber','addr:street','addr:suburb']).strip(),'source':f"https://www.openstreetmap.org/{item['type']}/{item['id']}"})
 return rows

def lookup(lat,lon,radius):
 rows=[];warnings=[];sources=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
  futures={pool.submit(task,lat,lon,radius):label for task,label in [(official_branches,'Martin’s official branch locator'),(map_branches,'OpenStreetMap directory')]}
  for future in concurrent.futures.as_completed(futures):
   label=futures[future]
   try:rows.extend(future.result());sources.append(label)
   except Exception:warnings.append(label+' unavailable; coverage is incomplete.')
 return {'competitors':rows,'source':'; '.join(sources),'warnings':warnings,'status':'partial' if sources else 'unavailable'}
