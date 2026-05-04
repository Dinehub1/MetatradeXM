from werkzeug.serving import run_simple
from werkzeug.wrappers import Request, Response
from threading import Thread
import requests
import time

@Request.application
def application(request):
    print("Received path:", request.path)
    return Response('Hello World!')

t = Thread(target=run_simple, args=('localhost', 4000, application))
t.daemon = True
t.start()
time.sleep(1)

print("SENDING WITH %23")
requests.get("http://localhost:4000/tick/GOLD.i%23")
