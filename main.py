from fastapi import FastAPI, File, UploadFile, HTTPException
import openai
from pypdf import PdfReader
import tempfile
import json
import sqlite3
import uuid
import random
import os
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from fastapi.staticfiles import StaticFiles

# -----------------------------
# CONFIG
# -----------------------------
openai.api_key = ""
DB_PATH = "interview.db"
QUESTION_BANK_FILE = "question_bank.json"

app = FastAPI()

# Create static folder if missing
if not os.path.exists("static"):
    os.makedirs("static")

# -----------------------------
# ROLE KEYWORDS
# -----------------------------
ROLE_KEYWORDS = {
    "software_engineer": [
        "Python", "Java", "C++", "Git", "APIs", "SQL", "OOP", "Debugging", "Agile"
    ],
    "cybersecurity": [
        "Risk Assessment", "Incident Response", "Firewall", "IDS", "IPS",
        "Threat Analysis", "NIST", "Vulnerability"
    ],
    "data_analyst": [
        "SQL", "Python", "Excel", "PowerBI", "Tableau", "EDA", "Regression", "Statistics"
    ]
}

# -----------------------------
# DATABASE INITIALIZATION
# -----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Sessions table (stores state)
    c.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        role TEXT,
        current_question_index INTEGER,
        is_finished INTEGER DEFAULT 0,
        used_questions TEXT DEFAULT '[]'
    )
    """)

    # Add used_questions column if missing
    c.execute("PRAGMA table_info(sessions)")
    columns = [col[1] for col in c.fetchall()]
    if "used_questions" not in columns:
        c.execute("ALTER TABLE sessions ADD COLUMN used_questions TEXT DEFAULT '[]'")

    # History of the Q&A
    c.execute("""
    CREATE TABLE IF NOT EXISTS interview_history (
        session_id TEXT,
        question TEXT,
        answer TEXT,
        evaluation_json TEXT
    )
    """)

    conn.commit()
    conn.close()


init_db()


# -----------------------------
# SPEECH-TO-TEXT (WHISPER)
# -----------------------------
@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()

    result = openai.audio.transcriptions.create(
        model="whisper-1",
        file=("audio.m4a", audio_bytes)
    )

    return {"transcript": result.text}



# -----------------------------
# SCORE ANSWER
# -----------------------------
@app.post("/score-answer")
async def score_answer(answer: str, question: str, role: str = "General"):
    prompt = f"""
Evaluate the following interview answer. Return ONLY JSON:

{{
  "score": 0-100,
  "strengths": [],
  "weaknesses": [],
  "skill_match": 0-100,
  "communication_score": 0-100,
  "final_feedback": ""
}}

Question: {question}
Answer: {answer}
Role: {role}
"""

    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Strict interview evaluator."},
            {"role": "user", "content": prompt}
        ]
    )

    raw = response.choices[0].message.content

    try:
        evaluation = json.loads(raw)
    except:
        start = raw.find("{")
        end = raw.rfind("}")
        evaluation = json.loads(raw[start:end+1])

    return evaluation


# -----------------------------
# ADAPTIVE NEXT-QUESTION LOGIC
# -----------------------------
@app.post("/next-question")
async def next_question(question: str, answer: str, evaluation: dict, role: str):
    prompt = f"""
You are an adaptive AI interviewer.
Return ONLY JSON:

{{
  "action": "follow_up" | "clarify" | "next" | "challenge",
  "question": ""
}}

RULES:
- score < 50 → follow-up
- communication_score < 50 → clarify
- score > 75 → next
- weaknesses that mention "detail" → ask for example

QUESTION: {question}
ANSWER: {answer}
EVALUATION: {evaluation}
ROLE: {role}
"""

    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Adaptive interviewer."},
            {"role": "user", "content": prompt}
        ]
    )

    raw = response.choices[0].message.content

    try:
        result = json.loads(raw)
    except:
        start = raw.find("{")
        end = raw.rfind("}")
        result = json.loads(raw[start:end+1])

    return result


# -----------------------------
# START INTERVIEW (RANDOM FIRST QUESTION)
# -----------------------------
@app.post("/start-interview")
async def start_interview(role: str):
    with open(QUESTION_BANK_FILE, "r", encoding="utf-8") as f:
        bank = json.load(f)

    role = role.lower().replace(" ", "_")

    if role not in bank:
        return {"error": "Invalid role"}

    session_id = str(uuid.uuid4())
    questions = bank[role]

    # Pick random first question
    first_index = random.randint(0, len(questions) - 1)
    first_question = questions[first_index]["question"]

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO sessions (session_id, role, current_question_index, used_questions)
        VALUES (?, ?, ?, ?)
    """, (session_id, role, first_index, json.dumps([first_index])))

    conn.commit()
    conn.close()

    return {"session_id": session_id, "question": first_question}


# -----------------------------
# SUBMIT ANSWER
# -----------------------------
@app.post("/submit-answer")
async def submit_answer(session_id: str, answer: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT role, used_questions FROM sessions WHERE session_id = ?", (session_id,))
    row = c.fetchone()
    if not row:
        return {"error": "Invalid session"}

    role, used_raw = row
    used_list = json.loads(used_raw)

    with open(QUESTION_BANK_FILE, "r", encoding="utf-8") as f:
        bank = json.load(f)

    current_question = bank[role][used_list[-1]]["question"]

    # SCORE ANSWER
    evaluation = await score_answer(answer, current_question, role)

    # Save to DB
    c.execute(
        "INSERT INTO interview_history (session_id, question, answer, evaluation_json) VALUES (?, ?, ?, ?)",
        (session_id, current_question, answer, json.dumps(evaluation))
    )
    conn.commit()
    conn.close()

    # Ask next question / follow-up, etc.
    next_action = await next_question(current_question, answer, evaluation, role)

    return {"evaluation": evaluation, "next_action": next_action}


# -----------------------------
# GET NEXT RANDOM QUESTION
# -----------------------------
@app.post("/get-next-question")
async def get_next_question(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT role, used_questions FROM sessions WHERE session_id = ?", (session_id,))
    row = c.fetchone()

    if not row:
        return {"error": "Invalid session"}

    role, used_raw = row
    used_list = json.loads(used_raw)

    with open(QUESTION_BANK_FILE, "r", encoding="utf-8") as f:
        bank = json.load(f)

    questions = bank[role]

    if len(used_list) >= len(questions):
        c.execute("UPDATE sessions SET is_finished = 1 WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()
        return {"message": "Interview complete"}

    # Choose unused question index
    unused_indices = [i for i in range(len(questions)) if i not in used_list]
    next_index = random.choice(unused_indices)
    next_question = questions[next_index]["question"]

    used_list.append(next_index)
    c.execute("UPDATE sessions SET used_questions = ? WHERE session_id = ?",
              (json.dumps(used_list), session_id))

    conn.commit()
    conn.close()

    return {"question": next_question}


# -----------------------------
# FINAL SUMMARY REPORT
# -----------------------------
@app.post("/final-report")
async def final_report(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT evaluation_json FROM interview_history WHERE session_id = ?", (session_id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return {"error": "No evaluation data found"}

    evaluations = [json.loads(r[0]) for r in rows]

    prompt = f"""
Create a final interview summary report based ONLY on these evaluations.

Return JSON ONLY in this format:

{{
  "overall_score": 0-100,
  "strengths_summary": "",
  "weaknesses_summary": "",
  "role_fit": "",
  "recommendations": ""
}}

Evaluations: {evaluations}
"""

    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Interview summary generator."},
            {"role": "user", "content": prompt}
        ]
    )

    raw = response.choices[0].message.content

    # ------------------------
    # SAFE JSON EXTRACTION
    # ------------------------
    try:
        return json.loads(raw)
    except:
        start = raw.find("{")
        end = raw.rfind("}")

        if start == -1 or end == -1:
            return {"error": "Invalid JSON returned", "raw_output": raw}

        try:
            return json.loads(raw[start:end+1])
        except:
            return {"error": "Failed to parse JSON", "raw_output": raw}


# ============================================================
#  1️⃣ PARSE CV
# ============================================================
@app.post("/cv-parse")
async def cv_parse(cv: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await cv.read())
        path = tmp.name

    reader = PdfReader(path)
    text = ""
    for p in reader.pages:
        extracted = p.extract_text()
        if extracted:
            text += extracted + "\n"

    if len(text.strip()) < 30:
        raise HTTPException(400, "Text extraction failed. PDF might be scanned.")

    prompt = f"""
Extract structured CV information.
Return ONLY JSON:

{{
  "personal_info": {{"name": "", "email": "", "phone": "", "location": ""}},
  "education": [],
  "experience": [],
  "projects": [],
  "skills": [],
  "certifications": [],
  "tools": [],
  "raw_text": ""
}}

CV TEXT:
{text}
"""

    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.choices[0].message.content

    try:
        parsed = json.loads(raw)
    except:
        parsed = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])

    parsed["raw_text"] = text
    os.unlink(path)

    return parsed


# ============================================================
#  2️⃣ EVALUATE CV (ATS + KEYWORDS + ROLE FIT)
# ============================================================
@app.post("/cv-evaluate")
async def cv_evaluate(role: str, parsed_cv: dict):

    role = role.lower().replace(" ", "_")

    if role not in ROLE_KEYWORDS:
        raise HTTPException(400, "Unknown role")

    keywords = ROLE_KEYWORDS[role]

    prompt = f"""
You are an ATS scoring engine.
Return STRICT JSON:

{{
  "ats": {{
      "ats_score": 0,
      "issues": [],
      "missing_sections": []
  }},
  "skills": {{
      "detected": [],
      "missing": [],
      "keyword_match": 0
  }},
  "role_fit": {{
      "score": 0,
      "reason": ""
  }}
}}

EXPECTED KEYWORDS: {keywords}

CV DATA: {json.dumps(parsed_cv)}
"""

    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.choices[0].message.content

    evaluation = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])

    return evaluation


# ============================================================
#  3️⃣ FULL ANALYSIS (PARSE + EVALUATE)
# ============================================================
@app.post("/cv-full-analysis")
async def cv_full_analysis(role: str, cv: UploadFile = File(...)):

    # Step 1 — PARSE
    parsed = await cv_parse(cv)

    # Step 2 — EVALUATE
    evaluation = await cv_evaluate(role, parsed)

    return {
        "parsed": parsed,
        "evaluation": evaluation
    }



app.mount("/", StaticFiles(directory="static", html=True), name="static")

