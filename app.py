from flask import Flask, jsonify
import socket

app = Flask(__name__)

@app.route("/ping")
def ping():
    return jsonify({
        "message": "Réponse de App B via VNet",
        "status": "ok",
        "hostname": socket.gethostname()
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
