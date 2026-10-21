"""Bounded research of public HTML pages; findings always require comparison review."""
import re, time, html, urllib.request, urllib.parse, subprocess, sys, json, tempfile, os, datetime
from pathlib import Path
from html.parser import HTMLParser

class Page(HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.links=[];self.skip=0;self.link=None;self.images=[]
 def handle_starttag(self,tag,attrs):
  if tag in ('script','style','noscript'):self.skip+=1
  if tag=='a':self.link=[dict(attrs).get('href',''),'']
  if tag=='img':
   a=dict(attrs);self.images.append([a.get('src',''),a.get('alt','')]);
   if self.link:self.link[1]+=' '+a.get('alt','')
 def handle_endtag(self,tag):
  if tag in ('script','style','noscript'):self.skip=max(0,self.skip-1)
  if tag=='a' and self.link:self.links.append(self.link);self.link=None
 def handle_data(self,data):
  if not self.skip:self.parts.append(data)
  if self.link:self.link[1]+=' '+data

def excluded_context(context):
 return bool(re.search(r'premium|per month|monthly|\bpm\b|p\s*/\s*m|p\.m\b|cover|insured|sum assured|tombstone|headstone|urn only|cash payout|cash pay.out|cash benefit|funeral to the value|post.mortem|crematorium fee|municipal tariff|grave plot|listing|subscription|program booklet|memorial card|e.obituary',context,re.I))


def candidates(text):
 text=re.sub(r'\s+',' ',html.unescape(text));rows=[];seen=set()
 pattern=r'\bR\s*(\d{1,3}(?:[ ,]\d{3})+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)(?!\d)'
 for match in re.finditer(pattern,text,re.I):
  raw=match[1].replace(' ','')
  if re.search(r'[.,]\d{2}$',raw):raw=raw[:-3].replace(',','').replace('.','')+'.'+raw[-2:]
  else:raw=raw.replace(',','')
  try:price=float(raw)
  except ValueError:continue
  if not 0<price<=500000:continue
  context=text[max(0,match.start()-110):match.end()+160]
  excluded=excluded_context(context)
  cash=bool(re.search(r'cash|once.off|package|cremation|burial|funeral service',context,re.I))
  kind='Excluded: insurance / cover or other non-comparable amount' if excluded else 'Cash package candidate — confirm inclusions' if cash else 'Unclassified amount — confirm what it buys'
  before=text[max(0,match.start()-120):match.start()]
  labels=list(re.finditer(r'cremat\w*|burial|funeral',before,re.I));service='cremation' if labels and labels[-1][0].lower().startswith('cremat') else 'burial'
  key=(price,kind,service)
  if key not in seen:rows.append({'price':price,'context':context,'kind':kind,'excluded':excluded,'serviceType':service});seen.add(key)
  if len(rows)>=15:break
 return rows

def pdf_text(raw):
 result=subprocess.run([sys.executable,str(Path(__file__).with_name('pdf_price_text.py'))],input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=4,check=True)
 return json.loads(result.stdout)

MARKETING=r'cash|price|pricing|package|cremat|burial|brochure|pamphlet|flyer|promo|special|advert|leaflet|offer|services|downloads'
def ocr_text(raw,suffix):
 if os.name!='nt':raise ValueError('Windows OCR is unavailable on this computer')
 with tempfile.NamedTemporaryFile(suffix=suffix,delete=False)as target:target.write(raw);filename=target.name
 try:
  result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(Path(__file__).with_name('marketing_ocr.ps1')),'-Path',filename],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=18,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  return json.loads(result.stdout.decode('utf-8-sig'))
 finally:Path(filename).unlink(missing_ok=True)

def research(url,validate,redirect_handler,discover=True,ocr=False):
 validate(url);queue=[url];checked=[];errors=[];found=[];materials=[];started=time.monotonic()
 opener=urllib.request.build_opener(redirect_handler())
 def material(address,label,kind):
  if not any(m['url']==address for m in materials) and len(materials)<20:materials.append({'url':address,'label':label[:150] or kind,'kind':kind,'status':'Manual review required'})
 ocr_count=0
 while queue and len(checked)+len(errors)<7 and time.monotonic()-started<(30 if ocr else 20):
  address=queue.pop(0)
  try:
   validate(address)
   request=urllib.request.Request(address,headers={'User-Agent':'FranchiseCompassSA/1.0 public price research'})
   with opener.open(request,timeout=4) as response:
    final=response.geturl();validate(final);content_type=response.headers.get('Content-Type','').lower()
    raw=response.read(3000001)
   if len(raw)>3000000:raise ValueError('Material too large; manual review required')
   if 'application/pdf' in content_type or raw.startswith(b'%PDF'):
    material(final,'PDF brochure / price list','PDF')
    document=pdf_text(raw);text=document['text'];checked.append(final)
    entry=next(m for m in materials if m['url']==final)
    if not text.strip() and ocr and ocr_count<2:
     ocr_count+=1;document=ocr_text(raw,'.pdf');text=document['text'];kind='Scanned PDF / OCR — check against original'
    entry['status']=('OCR text checked — verify against original' if document.get('method') else 'Text checked')+(' — first pages only' if document.get('limited') else '') if text.strip() else 'Image-only / scanned PDF — manual review required'
    page=None;kind='Scanned PDF / OCR — check against original' if document.get('method') else 'PDF brochure'
   elif any(t in content_type for t in ('text/html','application/xhtml')):
    page=Page();page.feed(raw.decode('utf-8',errors='replace'));checked.append(final);text=' '.join(page.parts);kind='Website / marketing page'
   elif content_type.startswith('image/'):
    material(final,'Picture advertisement / flyer','Image');checked.append(final)
    if not ocr or ocr_count>=2:continue
    ocr_count+=1;document=ocr_text(raw,'.png' if 'png' in content_type else '.jpg');text=document['text'];page=None;kind='Image flyer / OCR — check against original';next(m for m in materials if m['url']==final)['status']='OCR text checked — verify original price, date and inclusions' if text.strip() else 'No readable text — manual review required'
   else:raise ValueError('Unsupported material; manual review required')
   for row in candidates(text):
    if not any(r['price']==row['price'] and r['kind']==row['kind'] and r.get('serviceType')==row.get('serviceType') for r in found):found.append({**row,'source':final,'sourceType':kind})
   if not page or not discover:continue
   for href,label in page.links:
    target=urllib.parse.urljoin(final,href).split('#')[0];parts=urllib.parse.urlsplit(target)
    if parts.scheme!='https' or parts.username or parts.password:continue
    same_site=parts.hostname==urllib.parse.urlsplit(url).hostname
    relevant=bool(re.search(MARKETING,href+' '+label,re.I))
    is_pdf=bool(re.search(r'\.pdf$',parts.path,re.I));is_image=bool(re.search(r'\.(png|jpe?g|webp)$',parts.path,re.I))
    social=parts.hostname in ('facebook.com','www.facebook.com','instagram.com','www.instagram.com')
    if social:material(target,label,'Public social page — login or image review may be needed')
    if relevant and (is_pdf or is_image or not same_site):material(target,label,'PDF' if is_pdf else 'Image' if is_image else 'Linked marketing material')
    if same_site and relevant and not parts.query and (not is_image or ocr) and target not in checked+queue and len(queue)<12:queue.append(target)
   for href,label in page.images:
    if re.search(MARKETING,href+' '+label,re.I):
     target=urllib.parse.urljoin(final,href)
     if urllib.parse.urlsplit(target).scheme=='https':
      material(target,label,'Image advertisement / flyer')
      if ocr and urllib.parse.urlsplit(target).hostname==urllib.parse.urlsplit(url).hostname and target not in checked+queue and len(queue)<12:queue.append(target)
   queue.sort(key=lambda address:0 if re.search(r'\.(pdf|png|jpe?g|webp)(?:$|\?)|price|cash|package|special',address,re.I)else 1)
  except Exception:
   errors.append({'url':address,'message':'Material could not be read automatically; open it for manual review or request a written quote.'})
   if re.search(r'\.pdf(?:$|\?)',address,re.I):material(address,'PDF brochure','PDF')
 return {'url':url,'candidates':found[:30],'materials':materials,'checkedPages':checked,'errors':errors,'status':'partial' if errors or queue else 'checked','limited':bool(queue),'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Public website and linked marketing material. Text PDFs: first 8 pages. With Windows OCR: up to two flyers or scanned PDFs (first 4 pages). OCR findings require original-image review. Search/check date is not publication date. Confirm current price, local availability, VAT and like-for-like inclusions.'}
