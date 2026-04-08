import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = BASE_DIR / "quiz.db"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            student_token TEXT NOT NULL UNIQUE,
            admin_token TEXT NOT NULL UNIQUE,
            questions_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            student_name TEXT NOT NULL,
            answers_json TEXT NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            submitted_at TEXT NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
        )
        """
    )
    conn.commit()
    conn.close()


def parse_csv_questions(csv_text: str):
    import csv
    import io

    required = [
        "question",
        "option_a",
        "option_b",
        "option_c",
        "option_d",
        "correct_option",
        "explanation",
    ]

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise ValueError("CSV is empty or missing header row.")

    fieldnames = [f.strip().lower() for f in reader.fieldnames]
    if fieldnames != required:
        raise ValueError(
            "CSV header must be exactly: "
            + ",".join(required)
        )

    questions = []
    for idx, raw_row in enumerate(reader, start=1):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw_row.items()}
        if not row["question"]:
            raise ValueError(f"Row {idx}: question is required.")
        options = {
            "A": row["option_a"],
            "B": row["option_b"],
            "C": row["option_c"],
            "D": row["option_d"],
        }
        if not all(options.values()):
            raise ValueError(f"Row {idx}: all 4 options are required.")
        correct = row["correct_option"].upper()
        if correct not in {"A", "B", "C", "D"}:
            raise ValueError(f"Row {idx}: correct_option must be A/B/C/D.")
        if not row["explanation"]:
            raise ValueError(f"Row {idx}: explanation is required.")

        questions.append(
            {
                "id": idx,
                "question": row["question"],
                "options": options,
                "correct_option": correct,
                "explanation": row["explanation"],
            }
        )

    if not questions:
        raise ValueError("CSV contains no questions.")
    return questions


class QuizHandler(BaseHTTPRequestHandler):
    server_version = "QuizServer/1.0"

    def _conn(self):
        return sqlite3.connect(DB_PATH)

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, filename: str):
        path = STATIC_DIR / filename
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        ext = path.suffix.lower()
        content_type = "text/plain; charset=utf-8"
        if ext == ".html":
            content_type = "text/html; charset=utf-8"
        elif ext == ".css":
            content_type = "text/css; charset=utf-8"
        elif ext == ".js":
            content_type = "application/javascript; charset=utf-8"

        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/":
            self._send_file("index.html")
            return
        if route == "/admin":
            self._send_file("admin.html")
            return
        if route == "/monitor":
            self._send_file("monitor.html")
            return
        if route.startswith("/static/"):
            self._send_file(route.replace("/static/", "", 1))
            return
        if route.startswith("/api/quiz/"):
            student_token = route.split("/api/quiz/", 1)[1].strip()
            self._get_quiz(student_token)
            return
        if route.startswith("/api/results/"):
            quiz_token = route.split("/api/results/", 1)[1].strip()
            self._get_results(quiz_token, parse_qs(parsed.query))
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Route not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/api/create-quiz":
            self._create_quiz()
            return
        if route.startswith("/api/submit/"):
            student_token = route.split("/api/submit/", 1)[1].strip()
            self._submit_quiz(student_token)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Route not found")

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Request body is empty.")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON body.") from exc

    def _create_quiz(self):
        try:
            payload = self._read_json_body()
            title = (payload.get("title") or "").strip()
            csv_text = payload.get("csv_text") or ""
            if not title:
                raise ValueError("Quiz title is required.")
            questions = parse_csv_questions(csv_text)

            student_token = uuid.uuid4().hex[:16]
            admin_token = uuid.uuid4().hex

            conn = self._conn()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO quizzes (title, student_token, admin_token, questions_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    title,
                    student_token,
                    admin_token,
                    json.dumps(questions, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
            conn.commit()
            conn.close()

            host = self.headers.get("Host", "localhost:8000")
            scheme = self.headers.get("X-Forwarded-Proto", "http")
            base = f"{scheme}://{host}"
            self._send_json(
                {
                    "ok": True,
                    "student_link": f"{base}/?quiz={student_token}",
                    "monitor_link": f"{base}/monitor?quiz={student_token}&admin={admin_token}",
                    "question_count": len(questions),
                },
                HTTPStatus.CREATED,
            )
        except ValueError as err:
            self._send_json({"ok": False, "error": str(err)}, HTTPStatus.BAD_REQUEST)
        except Exception as err:  # pragma: no cover
            self._send_json({"ok": False, "error": str(err)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _get_quiz(self, student_token: str):
        conn = self._conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, questions_json FROM quizzes WHERE student_token = ?",
            (student_token,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            self._send_json({"ok": False, "error": "Quiz not found."}, HTTPStatus.NOT_FOUND)
            return

        quiz_id, title, questions_json = row
        questions = json.loads(questions_json)
        public_questions = [
            {
                "id": q["id"],
                "question": q["question"],
                "options": q["options"],
            }
            for q in questions
        ]
        self._send_json(
            {
                "ok": True,
                "quiz_id": quiz_id,
                "title": title,
                "questions": public_questions,
            }
        )

    def _submit_quiz(self, student_token: str):
        try:
            payload = self._read_json_body()
            student_name = (payload.get("student_name") or "").strip()
            answers = payload.get("answers")
            if not student_name:
                raise ValueError("Student name is required.")
            if not isinstance(answers, dict):
                raise ValueError("Answers must be an object.")

            conn = self._conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, title, questions_json FROM quizzes WHERE student_token = ?",
                (student_token,),
            )
            row = cur.fetchone()
            if not row:
                conn.close()
                self._send_json({"ok": False, "error": "Quiz not found."}, HTTPStatus.NOT_FOUND)
                return

            quiz_id, title, questions_json = row
            questions = json.loads(questions_json)
            details = []
            score = 0

            for q in questions:
                qid = str(q["id"])
                selected = (answers.get(qid) or "").upper()
                correct = q["correct_option"]
                is_correct = selected == correct
                if is_correct:
                    score += 1
                details.append(
                    {
                        "question_id": q["id"],
                        "question": q["question"],
                        "selected_option": selected if selected in {"A", "B", "C", "D"} else None,
                        "correct_option": correct,
                        "correct_text": q["options"][correct],
                        "explanation": q["explanation"],
                        "is_correct": is_correct,
                    }
                )

            cur.execute(
                """
                INSERT INTO submissions (quiz_id, student_name, answers_json, score, total, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    quiz_id,
                    student_name,
                    json.dumps(answers, ensure_ascii=False),
                    score,
                    len(questions),
                    utc_now_iso(),
                ),
            )
            conn.commit()
            conn.close()

            self._send_json(
                {
                    "ok": True,
                    "title": title,
                    "student_name": student_name,
                    "score": score,
                    "total": len(questions),
                    "details": details,
                }
            )
        except ValueError as err:
            self._send_json({"ok": False, "error": str(err)}, HTTPStatus.BAD_REQUEST)
        except Exception as err:  # pragma: no cover
            self._send_json({"ok": False, "error": str(err)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _get_results(self, student_token: str, query):
        admin_token = (query.get("admin", [""])[0] or "").strip()
        if not admin_token:
            self._send_json({"ok": False, "error": "Missing admin token."}, HTTPStatus.UNAUTHORIZED)
            return

        conn = self._conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, title
            FROM quizzes
            WHERE student_token = ? AND admin_token = ?
            """,
            (student_token, admin_token),
        )
        quiz = cur.fetchone()
        if not quiz:
            conn.close()
            self._send_json({"ok": False, "error": "Unauthorized or quiz not found."}, HTTPStatus.UNAUTHORIZED)
            return

        quiz_id, title = quiz
        cur.execute(
            """
            SELECT student_name, score, total, submitted_at
            FROM submissions
            WHERE quiz_id = ?
            ORDER BY submitted_at DESC
            """,
            (quiz_id,),
        )
        rows = cur.fetchall()
        conn.close()

        submissions = [
            {
                "student_name": r[0],
                "score": r[1],
                "total": r[2],
                "submitted_at": r[3],
            }
            for r in rows
        ]
        avg = round(sum(r["score"] for r in submissions) / len(submissions), 2) if submissions else 0

        self._send_json(
            {
                "ok": True,
                "title": title,
                "submissions_count": len(submissions),
                "average_score": avg,
                "submissions": submissions,
            }
        )


def run():
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), QuizHandler)
    print(f"Quiz server running on 0.0.0.0:{port}")
    print(f"Admin page: http://localhost:{port}/admin")
    server.serve_forever()


if __name__ == "__main__":
    run()
