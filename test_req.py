import requests

try:
    print("Testing requests.get with %23")
    url = "http://127.0.0.1:5003/tick/GOLD.i%23"
    print("Requested URL:", url)
    
    # We use Request to see what gets prepared
    req = requests.Request('GET', url)
    prep = req.prepare()
    print("Prepared path:", prep.path_url)
except Exception as e:
    print(e)
