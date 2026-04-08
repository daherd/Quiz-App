# Arabic Quiz Platform (CSV-based)

This project gives you:
- Arabic quiz page for students.
- Easy quiz creation from CSV.
- Automatic grading.
- Explanations shown after submission.
- Results monitoring page for the teacher.

## 1) Run locally

```bash
python server.py
```

Open:
- Admin page: `http://localhost:8000/admin`

From admin page you will get:
- Student link (send this to students)
- Monitor link (keep this private)

## 2) CSV format

Header must be exactly:

```csv
question,option_a,option_b,option_c,option_d,correct_option,explanation
```

- `correct_option` must be one of: `A`, `B`, `C`, `D`.
- Save file as UTF-8 to keep Arabic text correct.

Example CSV file is included: `sample_questions.csv`

## 3) Share with students

For internet access (outside your local network), deploy this project to a small hosting provider.
Easy options:
- [Render](https://render.com/)
- [Railway](https://railway.app/)

Deploy `server.py` and `static/` folder, then use the hosted URL.

## 4) Data storage

- Quiz and submissions are saved in `quiz.db` (SQLite).
- Keep backup of `quiz.db` if results are important.
