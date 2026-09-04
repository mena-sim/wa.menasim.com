import { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { api } from "../api.js";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "live", label: "Live" },
  { key: "waiting", label: "Waiting" },
  { key: "handoff", label: "Handed over" },
  { key: "closed", label: "Closed" },
];

function initials(s) {
  const digits = (s || "").replace(/[^0-9]/g, "");
  return digits.slice(-2) || (s || "?").slice(0, 2).toUpperCase();
}
function badgeClass(status) {
  if (status === "handoff") return "wait";
  if (status === "live") return "live";
  if (status === "waiting") return "wait";
  return "closed";
}
function statusLabel(status) {
  return status === "handoff" ? "handed over" : status;
}
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function Conversations({ toast }) {
  const [list, setList] = useState([]);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [convo, setConvo] = useState(null);
  const [reply, setReply] = useState("");
  const [mobileChat, setMobileChat] = useState(false);
  const msgsRef = useRef(null);

  async function loadList() {
    try {
      const data = await api.listConversations(filter, q, "all");
      setList(data.conversations);
    } catch (e) {
      /* polling errors are silent */
    }
  }

  async function loadConvo(id) {
    if (!id) return;
    try {
      const data = await api.getConversation(id);
      setConvo(data);
    } catch (e) {
      /* ignore */
    }
  }

  useEffect(() => {
    loadList();
    const t = setInterval(loadList, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, q]);

  useEffect(() => {
    if (!selectedId) return;
    loadConvo(selectedId);
    const t = setInterval(() => loadConvo(selectedId), 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [convo]);

  useEffect(() => {
    document.body.classList.toggle("view-chat-open", mobileChat);
    return () => document.body.classList.remove("view-chat-open");
  }, [mobileChat]);

  function select(id) {
    setSelectedId(id);
    setMobileChat(true);
  }

  async function toggleHandover() {
    if (!convo) return;
    const next = !convo.conversation.handed_over;
    try {
      await api.setHandover(convo.conversation.id, next);
      await loadConvo(convo.conversation.id);
      loadList();
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function sendReply() {
    const text = reply.trim();
    if (!text || !convo) return;
    setReply("");
    try {
      const res = await api.reply(convo.conversation.id, text);
      if (res.delivery && res.delivery.ok === false && !res.delivery.note) {
        toast("Saved, but delivery failed — check Twilio/Telnyx config.", true);
      }
      await loadConvo(convo.conversation.id);
      loadList();
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function closeConvo() {
    if (!convo) return;
    try {
      await api.closeConversation(convo.conversation.id);
      await loadConvo(convo.conversation.id);
      loadList();
      toast("Conversation closed.");
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function saveToKb() {
    if (!convo) return;
    try {
      const res = await api.saveConversationToKb(convo.conversation.id);
      toast(`Saved to knowledge base → ${res.source}. KB now ${res.chunks} chunks.`);
    } catch (e) {
      toast(e.message, true);
    }
  }

  const handedOver = convo?.conversation.handed_over;
  const liveCount = list.filter((c) => c.status === "live").length;
  const handoffCount = list.filter((c) => c.status === "handoff").length;

  return (
    <>
      <div className="sessions" style={mobileChat ? { display: undefined } : undefined}>
        <div className="head">
          <h2>Inbox</h2>
          <div className="search-wrap">
            <Icon name="search" />
            <input
              className="search"
              placeholder="Search by number, name, message…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
        </div>
        <div className="filters">
          {FILTERS.map((f) => (
            <div
              key={f.key}
              className={`chip ${filter === f.key ? "active" : ""}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
            </div>
          ))}
        </div>
        <div className="session-list">
          {list.length === 0 && (
            <div className="empty-note">
              No conversations yet. Send a WhatsApp or SMS to your business number, or use Settings → WhatsApp / Twilio to test.
            </div>
          )}
          {list.map((s) => (
            <div
              key={s.id}
              className={`session-item ${s.id === selectedId ? "active" : ""}`}
              onClick={() => select(s.id)}
            >
              <div className="avatar">{initials(s.sender_id || s.name)}</div>
              <div className="session-meta">
                <div className="row1">
                  <span>{s.sender_id || s.name}</span>
                  <span className={`badge ${badgeClass(s.status)}`}>{statusLabel(s.status)}</span>
                </div>
                <div className="row2">{s.channel === "sms" ? "SMS" : s.channel === "whatsapp" ? "WhatsApp" : s.channel} · {s.last || "…"}</div>
                {s.handed_over && (
                  <span className="handoff-flag">
                    <Icon name="handoff" /> Human replying
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="main">
        <div className="topbar">
          <div style={{ display: "flex", alignItems: "center" }}>
            <button className="mobile-back" onClick={() => setMobileChat(false)}>
              <Icon name="back" />
            </button>
            <div>
              <h1>Customer chats</h1>
              <div className="sub">
                {list.length} conversations · {liveCount} live · {handoffCount} handed over
              </div>
            </div>
          </div>
        </div>
        <div className="content">
          {!convo ? (
            <div className="placeholder-view">Select a conversation to view messages.</div>
          ) : (
            <div className="chat-view">
              <div className="chat-header">
                <div className="who">
                  <div className="avatar">{initials(convo.conversation.name)}</div>
                  <div>
                    <div className="name">{convo.conversation.name}</div>
                    <div className="num">
                      {convo.conversation.channel} · status: {statusLabel(convo.conversation.status)}
                    </div>
                  </div>
                </div>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <button className="btn secondary" onClick={saveToKb} title="Distill this chat into a KB entry (personal data stripped)">Save to KB</button>
                  <button className="btn secondary" onClick={closeConvo}>Close</button>
                  <div className="handover-toggle">
                    <div className="label">
                      Hand over to me<small>Pauses AI replies on this chat</small>
                    </div>
                    <div className={`switch ${handedOver ? "on" : ""}`} onClick={toggleHandover}>
                      <div className="knob" />
                    </div>
                  </div>
                </div>
              </div>
              <div className="msgs" ref={msgsRef}>
                {handedOver && (
                  <div className="system-note">You took over this conversation — AI replies are paused</div>
                )}
                {convo.messages.map((m) => (
                  <div key={m.id} className={`msg ${m.from}`}>
                    {m.content}
                    {m.media_url && <img src={m.media_url} alt="attachment" />}
                    <span className="t">{fmtTime(m.created_at)}</span>
                  </div>
                ))}
              </div>
              <div className="composer">
                <input
                  placeholder={
                    handedOver
                      ? "Reply manually as human agent…"
                      : "Send a manual reply (auto hands over this chat)…"
                  }
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && sendReply()}
                />
                <button className="btn send" onClick={sendReply}>
                  <Icon name="send" /> Send
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
