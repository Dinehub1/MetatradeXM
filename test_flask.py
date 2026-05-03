from flask import Flask, request
app = Flask(__name__)
@app.route('/tick/<symbol>')
def tick(symbol):
    print("URL:", request.url)
    print("PATH:", request.path)
    return f"Symbol: {symbol}"
if __name__ == '__main__':
    app.run(port=5001)
