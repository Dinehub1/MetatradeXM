import requests
from urllib.parse import quote
print(requests.Request('GET', f"http://127.0.0.1:5001/tick/{quote('GOLD.i#', safe='')}").prepare().url)
