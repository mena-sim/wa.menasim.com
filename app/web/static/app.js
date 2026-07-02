const messagesEl = document.getElementById("messages");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("sendBtn");
const emojiBtn = document.getElementById("emojiBtn");
const emojiPanel = document.getElementById("emojiPanel");
const attachBtn = document.getElementById("attachBtn");
const fileInput = document.getElementById("fileInput");
const micBtn = document.getElementById("micBtn");
const recBar = document.getElementById("recBar");
const recTimeEl = document.getElementById("recTime");
const recCancel = document.getElementById("recCancel");
const recSend = document.getElementById("recSend");

const sessionId = (() => {
  let id = localStorage.getItem("menasim_session");
  if (!id) {
    id = "web-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem("menasim_session", id);
  }
  return id;
})();

function addMessage(role, text, mediaUrl) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (isArabic(text)) bubble.dir = "rtl";
  bubble.textContent = text;
  wrap.appendChild(bubble);
  if (mediaUrl) {
    const img = document.createElement("img");
    img.className = "qr";
    img.src = mediaUrl;
    img.alt = "QR code";
    wrap.appendChild(img);
  }
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

function isArabic(s) {
  return /[\u0600-\u06FF]/.test(s || "");
}

function setBusy(busy) {
  sendBtn.disabled = busy;
  if (attachBtn) attachBtn.disabled = busy;
  if (emojiBtn) emojiBtn.disabled = busy;
  if (micBtn) micBtn.disabled = busy;
  input.disabled = busy;
}

// WhatsApp-style: show the mic when the text box is empty, Send when typing.
function updateComposerMode() {
  const hasText = input.value.trim().length > 0;
  if (micBtn) micBtn.hidden = hasText;
  sendBtn.hidden = !hasText;
}

async function send(text, isImage) {
  if (!text && !isImage) return;
  addMessage("user", isImage ? "[sent an image]" : text);
  setBusy(true);
  const typing = addMessage("assistant", "…");
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        session_id: sessionId,
        is_image: !!isImage,
        media_url: isImage ? "https://example.com/screenshot.jpg" : null,
      }),
    });
    const data = await res.json();
    typing.parentElement.remove();
    addMessage("assistant", data.reply, data.media_url);
  } catch (e) {
    typing.textContent = "Network error. Is the server running?";
  } finally {
    setBusy(false);
    input.focus();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  input.value = "";
  updateComposerMode();
  send(text, false);
});

input.addEventListener("input", updateComposerMode);

// ---------------- Emoji picker ----------------
const EMOJIS = [
  "😀","😁","😂","🤣","😊","😍","😘","😎","🤩","🥳",
  "👍","👏","🙏","🙌","💪","🤝","👌","✌️","🤙","👋",
  "❤️","🧡","💛","💚","💙","💜","🔥","✨","⭐","🎉",
  "😅","😉","🙂","😇","🤔","😴","😭","😡","😩","😱",
  "📱","📶","🌍","✈️","🧳","📲","✅","❌","⚠️","❓",
];

function buildEmojiPanel() {
  if (!emojiPanel || emojiPanel.dataset.built) return;
  EMOJIS.forEach((e) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "emoji";
    b.textContent = e;
    b.addEventListener("click", () => {
      input.value += e;
      input.focus();
      updateComposerMode();
    });
    emojiPanel.appendChild(b);
  });
  emojiPanel.dataset.built = "1";
}

if (emojiBtn) {
  emojiBtn.addEventListener("click", () => {
    buildEmojiPanel();
    emojiPanel.hidden = !emojiPanel.hidden;
  });
  document.addEventListener("click", (e) => {
    if (!emojiPanel || emojiPanel.hidden) return;
    if (!emojiPanel.contains(e.target) && e.target !== emojiBtn && !emojiBtn.contains(e.target)) {
      emojiPanel.hidden = true;
    }
  });
}

// ---------------- Attach / upload a screenshot ----------------
if (attachBtn) attachBtn.addEventListener("click", () => fileInput.click());
if (fileInput) {
  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    if (file) sendImage(file);
    fileInput.value = "";
  });
}

function addImageMessage(role, url) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const img = document.createElement("img");
  img.className = "qr";
  img.src = url;
  img.alt = "image";
  wrap.appendChild(img);
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrap;
}

async function sendImage(file) {
  addImageMessage("user", URL.createObjectURL(file));
  setBusy(true);
  const typing = addMessage("assistant", "…");
  try {
    const fd = new FormData();
    fd.append("file", file, file.name || "screenshot.png");
    fd.append("session_id", sessionId);
    const res = await fetch("/api/chat/image", { method: "POST", body: fd });
    const data = await res.json();
    typing.parentElement.remove();
    addMessage("assistant", (data && data.reply) || "Thanks, I've received your screenshot.", data && data.media_url);
  } catch (e) {
    typing.textContent = "Upload failed. Is the server running?";
  } finally {
    setBusy(false);
    input.focus();
  }
}

// ---------------- Voice notes (record -> upload -> transcribe) ----------------
let mediaRecorder = null;
let mediaStream = null;
let chunks = [];
let recTimer = null;
let recStart = 0;

function showRecBar(on) {
  recBar.hidden = !on;
  form.hidden = on;
}

function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function tickTimer() {
  recTimeEl.textContent = fmtElapsed(Date.now() - recStart);
}

async function startRecording() {
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    addMessage("assistant", "Voice recording isn't supported in this browser.");
    return;
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    addMessage("assistant", "I couldn't access the microphone. Please allow mic permission.");
    return;
  }
  chunks = [];
  const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
  mediaRecorder = mime ? new MediaRecorder(mediaStream, { mimeType: mime }) : new MediaRecorder(mediaStream);
  mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  mediaRecorder.start();
  recStart = Date.now();
  recTimeEl.textContent = "0:00";
  recTimer = setInterval(tickTimer, 250);
  showRecBar(true);
}

function stopTracks() {
  if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
  mediaStream = null;
}

function finishRecording(shouldSend) {
  clearInterval(recTimer);
  showRecBar(false);
  if (!mediaRecorder) { stopTracks(); return; }
  const type = mediaRecorder.mimeType || "audio/webm";
  mediaRecorder.onstop = () => {
    stopTracks();
    if (shouldSend && chunks.length) {
      sendVoice(new Blob(chunks, { type }));
    }
    chunks = [];
    mediaRecorder = null;
  };
  if (mediaRecorder.state !== "inactive") mediaRecorder.stop();
  else { stopTracks(); mediaRecorder = null; }
}

async function sendVoice(blob) {
  const userBubble = addMessage("user", "🎤 Voice note…");
  setBusy(true);
  const typing = addMessage("assistant", "…");
  try {
    const fd = new FormData();
    const ext = (blob.type.includes("ogg")) ? "ogg" : "webm";
    fd.append("file", blob, `voice.${ext}`);
    fd.append("session_id", sessionId);
    const res = await fetch("/api/chat/voice", { method: "POST", body: fd });
    const data = await res.json();
    typing.parentElement.remove();
    if (res.ok && data.transcript) {
      userBubble.textContent = "🎤 " + data.transcript;
      if (isArabic(data.transcript)) userBubble.dir = "rtl";
    } else if (!res.ok) {
      userBubble.textContent = "🎤 Voice note";
    }
    addMessage("assistant", (data && data.reply) || "Sorry, something went wrong with the voice note.", data && data.media_url);
  } catch (e) {
    typing.textContent = "Voice upload failed. Is the server running?";
  } finally {
    setBusy(false);
    input.focus();
  }
}

if (micBtn) micBtn.addEventListener("click", startRecording);
recSend.addEventListener("click", () => finishRecording(true));
recCancel.addEventListener("click", () => finishRecording(false));

updateComposerMode();
