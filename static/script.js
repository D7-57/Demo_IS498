let sessionId = null;
let mediaRecorder;
let audioChunks = [];
let mode = null;

// ----------------------
// MODE SELECTION
// ----------------------
function selectMode(m) {
    mode = m;

    document.getElementById("mode-selection").classList.add("hidden");

    if (m === "interview") {
        document.getElementById("role-section").classList.remove("hidden");
    } else if (m === "cv") {
        document.getElementById("cv-section").classList.remove("hidden");
    }
}

// ----------------------
// START INTERVIEW
// ----------------------
async function startInterview() {
    const role = document.getElementById("role-select").value;

    const res = await fetch("/start-interview?role=" + role, { method: "POST" });
    const data = await res.json();

    sessionId = data.session_id;

    document.getElementById("role-section").classList.add("hidden");
    document.getElementById("interview-section").classList.remove("hidden");

    document.getElementById("question-text").innerText = data.question;
}

// ----------------------
// RECORD AUDIO
// ----------------------
async function startRecording() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.start();

    mediaRecorder.ondataavailable = e => audioChunks.push(e.data);

    document.getElementById("record-btn").disabled = true;
    document.getElementById("stop-btn").disabled = false;
}

async function stopRecording() {
    mediaRecorder.stop();

    document.getElementById("record-btn").disabled = false;
    document.getElementById("stop-btn").disabled = true;
}

// ----------------------
// SUBMIT ANSWER
// ----------------------
async function submitAnswer() {
    const blob = new Blob(audioChunks, { type: "audio/webm" });

    const formData = new FormData();
    formData.append("audio", blob, "answer.webm");

    const transcribeRes = await fetch("/transcribe", {
        method: "POST",
        body: formData
    });

    const transcriptData = await transcribeRes.json();
    const transcript = transcriptData.transcript;

    document.getElementById("transcript-box").value = transcript;

    const evalRes = await fetch(`/submit-answer?session_id=${sessionId}&answer=${encodeURIComponent(transcript)}`, {
        method: "POST"
    });

    const evalData = await evalRes.json();

    document.getElementById("evaluation-section").classList.remove("hidden");
    document.getElementById("evaluation-output").innerText = JSON.stringify(evalData, null, 2);
}

// ----------------------
// NEXT QUESTION
// ----------------------
async function nextQuestion() {
    const res = await fetch(`/get-next-question?session_id=${sessionId}`, {
        method: "POST"
    });

    const data = await res.json();

    if (data.message === "Interview complete") {
        finishInterview();
        return;
    }

    document.getElementById("evaluation-section").classList.add("hidden");
    document.getElementById("transcript-box").value = "";
    document.getElementById("question-text").innerText = data.question;
}

// ----------------------
// FINAL REPORT
// ----------------------
async function finishInterview() {
    const res = await fetch(`/final-report?session_id=${sessionId}`, {
        method: "POST"
    });

    const data = await res.json();

    document.getElementById("interview-section").classList.add("hidden");
    document.getElementById("evaluation-section").classList.add("hidden");

    document.getElementById("final-section").classList.remove("hidden");
    document.getElementById("final-output").innerText = JSON.stringify(data, null, 2);
}

// ----------------------
// CV ANALYSIS
// ----------------------
async function analyzeCV() {
    const file = document.getElementById("cv-file").files[0];
    if (!file) {
        alert("Please upload a PDF first!");
        return;
    }

    const formData = new FormData();
    formData.append("cv", file);

    const res = await fetch("/cv-parse", {
        method: "POST",
        body: formData
    });

    const data = await res.json();

    document.getElementById("cv-output").innerText = JSON.stringify(data, null, 2);
}

async function parseCV() {
    const file = document.getElementById("cv-file").files[0];
    if (!file) return alert("Upload a PDF first!");

    const formData = new FormData();
    formData.append("cv", file);

    const res = await fetch("/cv-parse", { method: "POST", body: formData });
    const data = await res.json();

    window.lastParsedCV = data; // Save for next steps
    document.getElementById("cv-output").innerText =
        JSON.stringify(data, null, 2);
}

async function evaluateCV() {
    if (!window.lastParsedCV)
        return alert("You must parse CV first!");

    const role = document.getElementById("cv-role-select").value;

    const res = await fetch(`/cv-evaluate?role=${role}`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(window.lastParsedCV)
    });

    const data = await res.json();
    window.lastEvaluation = data;

    document.getElementById("cv-output").innerText =
        JSON.stringify(data, null, 2);
}

async function fullCVAnalysis() {
    const file = document.getElementById("cv-file").files[0];
    const role = document.getElementById("cv-role-select").value;

    if (!file) return alert("Upload a CV first!");

    const formData = new FormData();
    formData.append("cv", file);

    const res = await fetch(`/cv-full-analysis?role=${role}`, {
        method: "POST",
        body: formData
    });

    const data = await res.json();

    document.getElementById("cv-output").innerText =
        JSON.stringify(data, null, 2);
}
