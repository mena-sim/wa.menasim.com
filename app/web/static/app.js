const messagesEl = document.getElementById("messages");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("sendBtn");
const imgBtn = document.getElementById("imgBtn");

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
  imgBtn.disabled = busy;
  input.disabled = busy;
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
  send(text, false);
});

imgBtn.addEventListener("click", () => {
  send("", true);
});
