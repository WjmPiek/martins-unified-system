"""Discover public price and marketing sources; results are never verified local quotes."""
import concurrent.futures,datetime,html,re,urllib.request,urllib.parse,xml.etree.ElementTree as ET
from .public_web import safe_url,SafeRedirect
def search_sources(query):
 address='https://www.bing.com/search?'+urllib.parse.urlencode({'format':'rss','q':query,'cc':'ZA','setlang':'en'})
 request=urllib.request.Request(address,headers={'User-Agent':'Mozilla/5.0 (compatible; FranchiseCompassSA public research)'})
 with urllib.request.build_opener(SafeRedirect()).open(request,timeout=7) as response:raw=response.read(1000000)
 root=ET.fromstring(raw)
 return[{'title':item.findtext('title') or '', 'url':item.findtext('link') or '', 'snippet':html.unescape(item.findtext('description') or '')}for item in root.findall('.//item')]
def discover(context,search=search_sources):
 if context.get('mode')=='competitors':
  from . import territory_competitors
  return territory_competitors.lookup(context)
 title=str(context.get('title','')).strip()[:120]
 if len(title)<2:raise ValueError('Select a town or geographical territory before searching.')
 province=str(context.get('province',''))[:40];areas=[str(x)[:70] for x in context.get('areas',[])[:4]]
 businesses=[str(x)[:100]for x in context.get('businesses',[])[:2]if x]
 place=' '.join([title,province,'South Africa'])
 queries=[f'funeral {place} cash package prices brochure flyer',f'cremation {place} prices special packages']
 if businesses:queries.append(f'{businesses[0]} funeral prices packages brochure')
 if areas:queries.append(f'funeral {areas[0]} {province} price flyer cash')
 rows=[];errors=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=4)as pool:
  futures={pool.submit(search,q):q for q in queries}
  for future in concurrent.futures.as_completed(futures):
   try:rows.extend(future.result())
   except Exception:errors.append('A public web search source was unavailable or blocked.')
 # A published regional service source supplements sparse public search results.
 # It remains separate from businesses located inside the selected boundary.
 if province.casefold()=='gauteng':rows.insert(0,{'title':'Please Cremate Me / Anmor Funerals — Gauteng regional service','url':'https://cremateme.co.za/','snippet':'Provider publishes a Gauteng service area. Confirm transport radius, extra charges and availability for the selected territory; this is not proof of a branch inside it.'})
 seen=set();sources=[];local_words=[w.casefold()for w in re.findall(r'[\w-]+',' '.join([title,*areas])) if len(w)>3 and w.casefold()not in {'park','north','south','east','west','central','view'}]
 for row in rows:
  address=row['url'];text=' '.join([row['title'],row['snippet'],address])
  if not re.search(r'funer|cremat|undertak|burial',text,re.I):continue
  host=urllib.parse.urlsplit(address).hostname or ''
  if host.endswith(('.co.uk','.com.au','.ca','.co.nz')):continue
  if province.casefold()=='gauteng' and re.search(r'cape town|western cape|durban|kwazulu|port elizabeth|eastern cape',text,re.I) and not re.search(r'gauteng|johannesburg|pretoria|nationwide|national',text,re.I):continue
  try:safe_url(address)
  except Exception:continue
  key=address.rstrip('/').casefold()
  if key in seen:continue
  seen.add(key);local=any(w in text.casefold()for w in local_words)
  sources.append({**row,'localMention':local,'locationStatus':'Area name mentioned; branch and package availability need confirmation' if local else 'Wider web source; location and selected-territory coverage unconfirmed'})
 sources.sort(key=lambda row:(not row['localMention'],not bool(re.search(r'price|cash|package|flyer|brochure|cremat',row['title']+' '+row['url'],re.I))))
 if sources and not any(row['localMention']for row in sources):errors.append('No area-specific source was returned; wider sources require local-availability confirmation.')
 return{'sources':sources[:8],'queries':queries,'errors':errors,'status':'partial' if errors else 'checked','checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scopeNote':'Search results can include regional or national operators. They are not confirmed businesses inside the selected boundary or verified comparable prices.'}
