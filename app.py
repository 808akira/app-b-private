from flask import Flask, jsonify
import socket, time, os

app = Flask(__name__)
START_TIME = time.time()

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "hostname": socket.gethostname()})

@app.route("/status")
def status():
    uptime = int(time.time() - START_TIME)
    return jsonify({
        "status": "ok",
        "hostname": socket.gethostname(),
        "uptime_seconds": uptime,
        "uptime_human": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
        "region": os.environ.get("REGION_NAME", "West Europe"),
        "timestamp": int(time.time()),
        "network": "private-vnet",
        "accessible_from_internet": False
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
