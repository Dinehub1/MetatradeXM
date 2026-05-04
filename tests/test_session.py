import requests
from urllib.parse import quote

s = requests.Session()
url = f"http://127.0.0.1:5003/tick/{quote('GOLD.i#', safe='')}"
print(url)
req = requests.Request('GET', url)
prep = s.prepare_request(req)
print(prep.url)
