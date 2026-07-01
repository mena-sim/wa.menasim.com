const ticketList = document.getElementById("ticketList");
const ticketDetail = document.getElementById("ticketDetail");
const statusFilter = document.getElementById("statusFilter");

async function loadTickets() {
  const status = statusFilter.value;
  const res = await fetch(`/api/inbox/tickets?status=${status}`);
  const data = await res.json();
  ticketList.innerHTML = "";
  if (!data.tickets.length) {
    ticketList.innerHTML = `<p class="muted">No ${status} tickets.</p>`;
    return;
  }
  data.tickets.forEach((t) => {
    const el = document.createElement("div");
    el.className = "ticket-card";
    el.innerHTML = `
      <div class="ticket-top">
        <span class="badge ${t.kind}">${t.kind}</span>
        <span class="badge status-${t.status}">${t.status}</span>
      </div>
      <div class="ticket-subject">#${t.id} ${escapeHtml(t.subject || "")}</div>
      <div class="muted small">${t.channel || "?"} · ${escapeHtml(t.sender_id || "")}</div>
    `;
    el.onclick = () => openConversation(t);
    ticketList.appendChild(el);
  });
}

async function openConversation(t) {
  const res = await fetch(`/api/inbox/conversations/${t.conversation_id}`);
  const data = await res.json();
  if (!data.found) {
    ticketDetail.innerHTML = `<p class="muted">Conversation not found.</p>`;
    return;
  }
  const msgs = data.messages
    .map((m) => {
      const media = m.media_url
        ? `<div><img class="qr" src="${m.media_url}" alt="media" /></div>`
        : "";
      return `<div class="msg ${m.role}"><div class="role">${m.role}</div><div class="text">${escapeHtml(
        m.content || ""
      )}</div>${media}</div>`;
    })
    .join("");
  ticketDetail.innerHTML = `
    <div class="detail-header">
      <div><strong>#${t.id} ${escapeHtml(t.subject || "")}</strong></div>
      <div class="muted small">${escapeHtml(t.details || "")}</div>
      <div class="muted small">Contact: ${escapeHtml(t.contact || "-")}</div>
      ${t.status === "open" ? `<button id="resolveBtn">Mark resolved</button>` : ""}
    </div>
    <div class="transcript">${msgs}</div>
  `;
  const btn = document.getElementById("resolveBtn");
  if (btn) {
    btn.onclick = async () => {
      await fetch(`/api/inbox/tickets/${t.id}/resolve`, { method: "POST" });
      await loadTickets();
      ticketDetail.innerHTML = `<p class="muted">Ticket resolved.</p>`;
    };
  }
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

statusFilter.onchange = loadTickets;
loadTickets();
setInterval(loadTickets, 15000);
