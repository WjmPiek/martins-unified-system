"""Public HTTPS requests only, including every redirect."""
import urllib.request,urllib.parse,ipaddress,socket
def safe_url(value):
 u=urllib.parse.urlsplit(value)
 if u.scheme!='https' or u.username or u.password or not u.hostname or u.port or '.' not in u.hostname:raise ValueError('Use a public HTTPS website.')
 for info in socket.getaddrinfo(u.hostname,443):
  if not ipaddress.ip_address(info[4][0]).is_global:raise ValueError('Private and local addresses cannot be checked.')
 return value
class SafeRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,request,fp,code,msg,headers,newurl):safe_url(newurl);return super().redirect_request(request,fp,code,msg,headers,newurl)
