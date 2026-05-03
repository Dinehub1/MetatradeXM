import urllib.parse
from flask import Flask, request

app = Flask(__name__)

@app.route('/tick/<symbol>', methods=['GET'])
def get_tick(symbol):
    print(f"Server received tick request for symbol: {repr(symbol)}")
    return f"Symbol is {symbol}"

if __name__ == '__main__':
    import threading
    import time
    import requests

    def run_server():
        app.run(port=5003, host='127.0.0.1')

    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    time.sleep(1)

    sym = "GOLD.i#"
    url = f"http://127.0.0.1:5003/tick/{urllib.parse.quote(sym, safe='')}"
    print(f"Client requesting URL: {url}")
    try:
        resp = requests.get(url)
        print(f"Status Code: {resp.status_code}")
        print(f"Response: {resp.text}")
    except Exception as e:
        print("Error:", e)
