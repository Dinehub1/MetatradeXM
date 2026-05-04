import requests
from flask import Flask
from threading import Thread
import time

app = Flask(__name__)
@app.route('/tick/<symbol>')
def tick(symbol):
    return "ok"

def run_server():
    app.run(port=5003)

t = Thread(target=run_server)
t.daemon = True
t.start()
time.sleep(1)

print("SENDING WITH %23")
requests.get("http://127.0.0.1:5003/tick/GOLD.i%23")

print("SENDING WITHOUT %23 (just #)")
requests.get("http://127.0.0.1:5003/tick/GOLD.i#")

