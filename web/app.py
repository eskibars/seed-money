"""Flask web application for Seed Money bracket optimizer."""

import json
import os
import sys
import threading
import time
import traceback
import uuid

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, send_from_directory

from web.database import init_db, get_db, get_job, get_queue_position, get_team_list
from web.refresh import refresh_all, refresh_bracket
from web.services import run_optimization, OUTPUT_DIR

ADMIN_KEY = os.environ.get("SEED_MONEY_ADMIN_KEY", "refresh")


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "seed-money-dev-key")

    # Initialize DB
    init_db()

    # Start background worker
    worker = threading.Thread(target=_worker_loop, args=(app,), daemon=True)
    worker.start()

    # --- Routes ---

    @app.route("/")
    def index():
        conn = get_db()
        teams = get_team_list(conn)
        conn.close()
        return render_template("index.html", teams=teams)

    @app.route("/optimize", methods=["POST"])
    def optimize():
        job_id = str(uuid.uuid4())

        # Parse form
        job_config = {
            "pool_size": request.form.get("pool_size", "7"),
            "scoring_preset": request.form.get("scoring_preset", "family"),
            "accuracy_weight": request.form.get("accuracy_weight", "0.75"),
            "sims": request.form.get("sims", "10000"),
            "force_champion": request.form.get("force_champion", "").strip(),
        }

        # Custom scoring
        if job_config["scoring_preset"] == "custom":
            for i in range(1, 7):
                job_config[f"round_{i}_pts"] = request.form.get(f"round_{i}_pts", str(i))

        # Upset bonus
        upset_mode = request.form.get("upset_mode", "none")
        if upset_mode in ("multiplier", "fixed"):
            job_config["upset_mode"] = upset_mode
            for i in range(1, 7):
                job_config[f"upset_r{i}"] = request.form.get(f"upset_r{i}", "0")

        # Biases
        biases = []
        bias_teams = request.form.getlist("bias_team")
        bias_directions = request.form.getlist("bias_direction")
        bias_magnitudes = request.form.getlist("bias_magnitude")
        for team, direction, magnitude in zip(bias_teams, bias_directions, bias_magnitudes):
            if team.strip():
                biases.append({
                    "team": team.strip(),
                    "direction": direction,
                    "magnitude": magnitude,
                })
        if biases:
            job_config["biases"] = biases

        conn = get_db()
        conn.execute(
            "INSERT INTO jobs (id, status, config_json) VALUES (?, 'queued', ?)",
            (job_id, json.dumps(job_config))
        )
        conn.commit()
        conn.close()

        return redirect(url_for("job_status", job_id=job_id))

    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(os.path.join(app.root_path, 'static'),
                                'favicon.ico', mimetype='image/vnd.microsoft.icon')

    @app.route("/jobs/<job_id>")
    def job_status(job_id):
        conn = get_db()
        job = get_job(conn, job_id)
        conn.close()

        if not job:
            return "Job not found", 404

        if job["status"] == "completed":
            return redirect(url_for("view_bracket", job_id=job_id))

        return render_template("queue.html", job_id=job_id, job=job)

    @app.route("/api/jobs/<job_id>")
    def api_job_status(job_id):
        conn = get_db()
        job = get_job(conn, job_id)
        if not job:
            conn.close()
            return jsonify({"error": "not found"}), 404

        position = get_queue_position(conn, job_id) if job["status"] == "queued" else 0
        conn.close()

        return jsonify({
            "status": job["status"],
            "position": position,
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            "error_message": job["error_message"],
        })

    @app.route("/brackets/<job_id>")
    def view_bracket(job_id):
        conn = get_db()
        job = get_job(conn, job_id)
        conn.close()

        if not job:
            return "Job not found", 404

        if job["status"] != "completed":
            return redirect(url_for("job_status", job_id=job_id))

        html_path = os.path.join(OUTPUT_DIR, f"{job_id}.html")
        if not os.path.exists(html_path):
            return "Bracket file not found", 404

        return send_file(html_path, mimetype="text/html")

    @app.route("/admin/refresh")
    def admin_refresh():
        key = request.args.get("key", "")
        if key != ADMIN_KEY:
            return "Unauthorized", 401

        year_param = request.args.get("year", "").strip()
        try:
            year = int(year_param) if year_param else time.localtime().tm_year
        except ValueError:
            return "Invalid year", 400

        conn = get_db()
        results = refresh_all(conn, year=year)

        # Also load the most relevant local bracket file if it exists
        bracket_path, bracket_year = _find_local_bracket_file(year)
        if os.path.exists(bracket_path):
            with open(bracket_path, "r") as f:
                bracket_data = json.load(f)
            refresh_bracket(conn, bracket_data, year=bracket_year)
            results["bracket"] = f"OK (loaded {os.path.basename(bracket_path)})"
        else:
            results["bracket"] = f"No local bracket file found for {year}"

        conn.close()
        return jsonify(results)

    @app.route("/api/teams")
    def api_teams():
        conn = get_db()
        teams = get_team_list(conn)
        conn.close()
        return jsonify(teams)

    return app


def _worker_loop(app):
    """Background worker that processes queued jobs."""
    with app.app_context():
        while True:
            try:
                conn = get_db()
                job = conn.execute(
                    "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()

                if job:
                    job_id = job["id"]
                    job_config = json.loads(job["config_json"])

                    conn.execute(
                        "UPDATE jobs SET status = 'running', started_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (job_id,)
                    )
                    conn.commit()

                    try:
                        run_optimization(job_id, job_config, conn)
                        conn.execute(
                            "UPDATE jobs SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (job_id,)
                        )
                        conn.commit()
                    except Exception as e:
                        conn.execute(
                            "UPDATE jobs SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ? WHERE id = ?",
                            (traceback.format_exc(), job_id)
                        )
                        conn.commit()

                conn.close()

            except Exception:
                pass

            time.sleep(2)


def _find_local_bracket_file(preferred_year: int) -> tuple[str, int]:
    """Find the best local bracket JSON file for refreshes."""
    raw_dir = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
    exact = os.path.join(raw_dir, f"bracket_{preferred_year}.json")
    if os.path.exists(exact):
        return exact, preferred_year

    generic = os.path.join(raw_dir, "bracket.json")
    if os.path.exists(generic):
        return generic, preferred_year

    fallback_year = preferred_year
    fallback_path = ""
    if os.path.isdir(raw_dir):
        for name in os.listdir(raw_dir):
            if not name.startswith("bracket_") or not name.endswith(".json"):
                continue
            year_text = name[len("bracket_"):-len(".json")]
            if not year_text.isdigit():
                continue
            file_year = int(year_text)
            if not fallback_path or file_year > fallback_year:
                fallback_year = file_year
                fallback_path = os.path.join(raw_dir, name)

    return fallback_path, fallback_year


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=17349)
