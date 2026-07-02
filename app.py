from flask import Flask, jsonify, request
import socket, time, os, random, threading
import networkx as nx
import psutil

app = Flask(__name__)
START_TIME = time.time()

# Active jobs stored in memory: job_id -> result or status
_jobs = {}
_jobs_lock = threading.Lock()


# ── existing endpoints (preserved) ──────────────────────────────────────────

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
        "accessible_from_internet": False,
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_mb": round(psutil.Process().memory_info().rss / 1024 / 1024, 1),
    })


# ── simulation engine ────────────────────────────────────────────────────────

def _build_graph(nodes: int, edges: int, seed: int) -> nx.Graph:
    rng = random.Random(seed)
    G = nx.Graph()
    G.add_nodes_from(range(nodes))
    node_list = list(range(nodes))
    # garantit la connexité : chaîne de base
    rng.shuffle(node_list)
    for i in range(len(node_list) - 1):
        w = round(rng.uniform(1, 100), 2)
        G.add_edge(node_list[i], node_list[i + 1], weight=w)
    # arêtes aléatoires supplémentaires
    added = 0
    attempts = 0
    target = min(edges - (nodes - 1), nodes * (nodes - 1) // 2 - (nodes - 1))
    while added < target and attempts < edges * 10:
        u, v = rng.sample(range(nodes), 2)
        if not G.has_edge(u, v):
            G.add_edge(u, v, weight=round(rng.uniform(1, 100), 2))
            added += 1
        attempts += 1
    return G


def _run_simulation(job_id: str, params: dict):
    t0 = time.time()
    nodes  = params["nodes"]
    edges  = params["edges"]
    algo   = params["algorithm"]
    seed   = params.get("seed", 42)
    source = params.get("source", 0)
    target_node = params.get("target", nodes - 1)

    proc = psutil.Process()
    cpu_before = psutil.cpu_percent(interval=None)

    try:
        G = _build_graph(nodes, edges, seed)
        result = {}

        if algo == "shortest_path":
            path = nx.dijkstra_path(G, source, target_node, weight="weight")
            length = nx.dijkstra_path_length(G, source, target_node, weight="weight")
            result = {
                "path": path,
                "path_length": round(length, 2),
                "hops": len(path) - 1,
            }

        elif algo == "betweenness":
            # O(n*m) — intentionnellement coûteux
            bc = nx.betweenness_centrality(G, weight="weight", normalized=True)
            top5 = sorted(bc.items(), key=lambda x: x[1], reverse=True)[:5]
            result = {
                "top_critical_nodes": [
                    {"node": n, "score": round(s, 4)} for n, s in top5
                ],
                "most_critical": top5[0][0],
            }

        elif algo == "failure_simulation":
            # Supprime des nœuds aléatoirement et recalcule la connexité
            rng2 = random.Random(seed + 1)
            scenarios = []
            for _ in range(params.get("failure_rounds", 20)):
                failed = rng2.sample(list(G.nodes()), k=max(1, nodes // 10))
                H = G.copy()
                H.remove_nodes_from(failed)
                connected = nx.is_connected(H) if len(H.nodes) > 0 else False
                scenarios.append({
                    "failed_nodes": failed,
                    "network_connected": connected,
                    "remaining_nodes": len(H.nodes),
                })
            failures = sum(1 for s in scenarios if not s["network_connected"])
            result = {
                "scenarios_tested": len(scenarios),
                "network_failures": failures,
                "resilience_score": round(1 - failures / len(scenarios), 3),
                "scenarios": scenarios[:5],  # sample pour la réponse
            }

        elif algo == "all":
            # Lance les trois — charge maximale
            path = nx.dijkstra_path(G, source, target_node, weight="weight")
            length = nx.dijkstra_path_length(G, source, target_node, weight="weight")
            bc = nx.betweenness_centrality(G, weight="weight", normalized=True)
            top5 = sorted(bc.items(), key=lambda x: x[1], reverse=True)[:5]
            rng2 = random.Random(seed + 1)
            scenarios = []
            for _ in range(params.get("failure_rounds", 20)):
                failed = rng2.sample(list(G.nodes()), k=max(1, nodes // 10))
                H = G.copy()
                H.remove_nodes_from(failed)
                connected = nx.is_connected(H) if len(H.nodes) > 0 else False
                scenarios.append({"network_connected": connected})
            failures = sum(1 for s in scenarios if not s["network_connected"])
            result = {
                "shortest_path": {"path": path, "length": round(length, 2), "hops": len(path) - 1},
                "betweenness": {"top_critical_nodes": [{"node": n, "score": round(s, 4)} for n, s in top5]},
                "resilience": {"score": round(1 - failures / len(scenarios), 3), "failures": failures},
            }

        duration = round(time.time() - t0, 3)
        mem_mb = round(proc.memory_info().rss / 1024 / 1024, 1)

        with _jobs_lock:
            _jobs[job_id] = {
                "status": "done",
                "job_id": job_id,
                "algorithm": algo,
                "nodes": nodes,
                "edges": G.number_of_edges(),
                "duration_seconds": duration,
                "memory_mb": mem_mb,
                "hostname": socket.gethostname(),
                "result": result,
            }

    except Exception as e:
        with _jobs_lock:
            _jobs[job_id] = {"status": "error", "job_id": job_id, "error": str(e)}


@app.route("/simulate", methods=["POST"])
def simulate():
    """
    Body JSON attendu :
    {
      "nodes": 200,
      "edges": 500,
      "algorithm": "shortest_path" | "betweenness" | "failure_simulation" | "all",
      "source": 0,         (optionnel)
      "target": 199,       (optionnel)
      "seed": 42,          (optionnel)
      "failure_rounds": 20 (optionnel, pour failure_simulation/all)
    }
    """
    data = request.get_json(silent=True) or {}

    nodes = int(data.get("nodes", 100))
    edges = int(data.get("edges", 300))
    algo  = data.get("algorithm", "shortest_path")

    if nodes < 2 or nodes > 1000:
        return jsonify({"error": "nodes doit être entre 2 et 1000"}), 400
    if algo not in ("shortest_path", "betweenness", "failure_simulation", "all"):
        return jsonify({"error": "algorithm invalide"}), 400

    job_id = f"job-{int(time.time()*1000)}-{random.randint(1000,9999)}"
    params = {**data, "nodes": nodes, "edges": edges, "algorithm": algo}

    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "job_id": job_id}

    thread = threading.Thread(target=_run_simulation, args=(job_id, params), daemon=True)
    thread.start()

    return jsonify({"status": "running", "job_id": job_id}), 202


@app.route("/jobs/<job_id>")
def get_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({"error": "job introuvable"}), 404
    return jsonify(job)


@app.route("/jobs")
def list_jobs():
    with _jobs_lock:
        jobs = [{"job_id": k, "status": v["status"]} for k, v in _jobs.items()]
    return jsonify({"jobs": jobs, "total": len(jobs)})


@app.route("/metrics")
def metrics():
    proc = psutil.Process()
    return jsonify({
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "memory_mb": round(proc.memory_info().rss / 1024 / 1024, 1),
        "memory_percent": round(proc.memory_percent(), 2),
        "active_jobs": sum(1 for v in _jobs.values() if v["status"] == "running"),
        "total_jobs": len(_jobs),
        "hostname": socket.gethostname(),
        "uptime_seconds": int(time.time() - START_TIME),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
