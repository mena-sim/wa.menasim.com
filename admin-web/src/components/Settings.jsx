import { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { api } from "../api.js";

const TABS = [
  { key: "deepseek", label: "DeepSeek LLM", icon: "brain" },
  { key: "telnyx", label: "Telnyx", icon: "telnyx" },
  { key: "whatsapp", label: "WhatsApp", icon: "whatsapp" },
  { key: "wordpress", label: "WordPress API", icon: "wordpress" },
  { key: "agent", label: "Agent & KB", icon: "book" },
  { key: "voice", label: "Voice notes", icon: "mic" },
  { key: "smtp", label: "Email Alerts", icon: "mail" },
];

const BUILTIN_SKILLS = [
  "Search knowledge base",
  "Look up order status",
  "Check eSIM data usage (ICCID)",
  "Check eSIM activation status",
  "Resend QR code",
  "Escalate to a human agent",
  "Create refund ticket",
];

function Field({ label, children, hint }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

function ConnStatus({ state }) {
  const cls =
    state.status === "ok" ? "ok" : state.status === "fail" ? "fail" : state.status === "testing" ? "pulse" : "";
  const text =
    state.status === "testing"
      ? "Testing…"
      : state.status === "ok"
      ? state.msg || "Connected"
      : state.status === "fail"
      ? state.msg || "Failed"
      : "Not tested";
  return (
    <div className="conn-status">
      <div className={`conn-dot ${cls}`} />
      <span>{text}</span>
    </div>
  );
}

export default function Settings({ toast }) {
  const [tab, setTab] = useState("deepseek");
  const [groups, setGroups] = useState(null);
  const [status, setStatus] = useState({});
  const [conn, setConn] = useState({});
  const [saving, setSaving] = useState("");
  const [waStatus, setWaStatus] = useState(null);
  const [testPhone, setTestPhone] = useState("");
  const [waBusy, setWaBusy] = useState("");

  async function load() {
    try {
      const data = await api.getConfig();
      setGroups(data.groups);
      setStatus(data.status);
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function loadWhatsappStatus() {
    try {
      const data = await api.getWhatsappStatus();
      setWaStatus(data);
    } catch (e) {
      toast(e.message, true);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (tab === "whatsapp" || tab === "telnyx") loadWhatsappStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  function setField(group, key, value) {
    setGroups((g) => ({ ...g, [group]: { ...g[group], [key]: value } }));
  }

  async function save(group) {
    setSaving(group);
    try {
      await api.saveConfig(group, groups[group]);
      await load();
      toast("Saved.");
    } catch (e) {
      toast(e.message, true);
    } finally {
      setSaving("");
    }
  }

  async function test(group) {
    setConn((c) => ({ ...c, [group]: { status: "testing" } }));
    try {
      const res = await api.testConfig(group);
      const extra = [res.message, ...(res.warnings || []), ...(res.details || [])].filter(Boolean).join(" · ");
      setConn((c) => ({
        ...c,
        [group]: { status: res.ok ? "ok" : "fail", msg: extra || res.message },
      }));
      if (group === "whatsapp") await loadWhatsappStatus();
    } catch (e) {
      setConn((c) => ({ ...c, [group]: { status: "fail", msg: e.message } }));
    }
  }

  async function autoConfigureWhatsapp() {
    setWaBusy("auto");
    try {
      const res = await api.autoConfigureWhatsapp();
      await load();
      await loadWhatsappStatus();
      toast(res.message, !res.ok);
      if (res.discover?.suggested_profile_id) {
        setConn((c) => ({
          ...c,
          whatsapp: {
            status: res.ok ? "ok" : "fail",
            msg: [
              res.message,
              res.discover.suggested_profile_id && `Profile: ${res.discover.suggested_profile_id}`,
              res.discover.suggested_waba_id && `WABA: ${res.discover.suggested_waba_id}`,
            ].filter(Boolean).join(" · "),
          },
        }));
      }
    } catch (e) {
      toast(e.message, true);
    } finally {
      setWaBusy("");
    }
  }

  async function runWhatsappTest(kind) {
    const phone = (testPhone || groups?.whatsapp?.telnyx_whatsapp_from || "").trim();
    if (!phone && kind !== "connect") {
      toast("Enter a test phone number (your mobile).", true);
      return;
    }
    setWaBusy(kind);
    try {
      let res;
      if (kind === "connect") {
        res = await api.testConfig("whatsapp");
        const extra = [res.message, ...(res.warnings || []), ...(res.details || [])].filter(Boolean).join(" · ");
        setConn((c) => ({ ...c, whatsapp: { status: res.ok ? "ok" : "fail", msg: extra || res.message } }));
      } else if (kind === "inbound") {
        res = await api.testWhatsappInbound(phone, "Hello, I need help with my eSIM.", false);
        toast(res.ok ? `Agent replied (conversation #${res.conversation_id})` : res.message, !res.ok);
      } else if (kind === "send") {
        res = await api.testWhatsappSend(
          phone,
          "Test from menasim WA support. If you see this, outbound WhatsApp is working."
        );
        toast(res.message, !res.ok);
      } else if (kind === "full") {
        res = await api.testWhatsappInbound(phone, "Hello, I need help with my eSIM.", true);
        toast(res.message, !res.ok);
      }
      await loadWhatsappStatus();
    } catch (e) {
      toast(e.message, true);
    } finally {
      setWaBusy("");
    }
  }

  if (!groups) {
    return (
      <div className="main">
        <div className="topbar">
          <div>
            <h1>Settings</h1>
            <div className="sub">Loading configuration…</div>
          </div>
        </div>
      </div>
    );
  }

  const g = groups;

  return (
    <div className="main">
      <div className="topbar">
        <div>
          <h1>Settings</h1>
          <div className="sub">API connections, WhatsApp number, and agent configuration</div>
        </div>
      </div>
      <div className="content">
        <div className="settings">
          <div className="tabs">
            {TABS.map((t) => (
              <div key={t.key} className={`tab ${tab === t.key ? "active" : ""}`} onClick={() => setTab(t.key)}>
                <Icon name={t.icon} /> {t.label}
              </div>
            ))}
          </div>

          {tab === "deepseek" && (
            <div className="card">
              <h3><Icon name="brain" /> DeepSeek LLM API</h3>
              <div className="desc">Powers the AI agent's replies, intent detection and escalation logic.</div>
              <Field label="DeepSeek API key">
                <input type="password" placeholder="sk-..." value={g.deepseek.deepseek_api_key}
                  onChange={(e) => setField("deepseek", "deepseek_api_key", e.target.value)} />
              </Field>
              <div className="row2col">
                <Field label="Base URL">
                  <input value={g.deepseek.deepseek_base_url}
                    onChange={(e) => setField("deepseek", "deepseek_base_url", e.target.value)} />
                </Field>
                <Field label="Model">
                  <select value={g.deepseek.deepseek_model}
                    onChange={(e) => setField("deepseek", "deepseek_model", e.target.value)}>
                    <option value="deepseek-chat">deepseek-chat</option>
                    <option value="deepseek-reasoner">deepseek-reasoner</option>
                  </select>
                </Field>
              </div>
              <div className="row2col">
                <Field label="Temperature">
                  <input value={g.deepseek.agent_temperature}
                    onChange={(e) => setField("deepseek", "agent_temperature", e.target.value)} />
                </Field>
                <Field label="Max tokens per reply">
                  <input value={g.deepseek.agent_max_tokens}
                    onChange={(e) => setField("deepseek", "agent_max_tokens", e.target.value)} />
                </Field>
              </div>
              <div className="field-actions">
                <button className="btn secondary" onClick={() => test("deepseek")}>Test connection</button>
                <ConnStatus state={conn.deepseek || {}} />
                <button className="btn primary" disabled={saving === "deepseek"} onClick={() => save("deepseek")}>
                  {saving === "deepseek" ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>
          )}

          {tab === "telnyx" && (
            <div className="card">
              <h3><Icon name="telnyx" /> Telnyx API</h3>
              <div className="desc">Used for WhatsApp messaging, number provisioning and delivery status webhooks.</div>
              <Field label="Telnyx API key">
                <input type="password" placeholder="KEY_live_..." value={g.telnyx.telnyx_api_key}
                  onChange={(e) => setField("telnyx", "telnyx_api_key", e.target.value)} />
              </Field>
              <Field label="Messaging profile" hint="This app only uses the Telnyx profile named WA 2-99. SMS, voxbulk, and ai-assistant profiles are ignored.">
                <input
                  readOnly
                  value={
                    (waStatus?.messaging_profiles || []).find((p) => p.name === "WA 2-99")?.id
                      ? `WA 2-99 (${(waStatus.messaging_profiles.find((p) => p.name === "WA 2-99") || {}).id})`
                      : g.telnyx.telnyx_messaging_profile_id
                        ? `WA 2-99 (${g.telnyx.telnyx_messaging_profile_id})`
                        : "WA 2-99 — run Auto-detect on WhatsApp tab"
                  }
                />
              </Field>
              <Field label="Webhook public key (Ed25519)">
                <input type="password" placeholder="base64 public key" value={g.telnyx.telnyx_webhook_public_key}
                  onChange={(e) => setField("telnyx", "telnyx_webhook_public_key", e.target.value)} />
              </Field>
              <Field label="Webhook URL (configure this in the Telnyx portal)" hint="Must point to wa.menasim.com — Telnyx sends inbound WhatsApp messages here.">
                <input readOnly value={status.webhook_url || "https://wa.menasim.com/telnyx/webhooks/messages"} />
              </Field>
              <div className="field-actions">
                <button className="btn secondary" onClick={() => test("telnyx")}>Test connection</button>
                <ConnStatus state={conn.telnyx || {}} />
                <button className="btn primary" disabled={saving === "telnyx"} onClick={() => save("telnyx")}>
                  {saving === "telnyx" ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>
          )}

          {tab === "whatsapp" && (
            <div className="card">
              <h3><Icon name="whatsapp" /> WhatsApp number</h3>
              <div className="desc">
                Customers message this number on WhatsApp. All support chats are handled by the AI agent on the WhatsApp channel.
              </div>
              <div className="row2col">
                <Field label="Sender (from) number" hint="E.164 format, e.g. +447822002099">
                  <input placeholder="+447822002099" value={g.whatsapp.telnyx_whatsapp_from}
                    onChange={(e) => setField("whatsapp", "telnyx_whatsapp_from", e.target.value)} />
                </Field>
                <Field label="Display name">
                  <input value={g.whatsapp.whatsapp_display_name}
                    onChange={(e) => setField("whatsapp", "whatsapp_display_name", e.target.value)} />
                </Field>
              </div>
              <Field label="Business account ID (WABA)" hint="Meta WhatsApp Business account ID, e.g. 1339285631627922. Reference only — filled by Auto-detect.">
                <input placeholder="1339285631627922" value={g.whatsapp.whatsapp_business_id}
                  onChange={(e) => setField("whatsapp", "whatsapp_business_id", e.target.value)} />
              </Field>
              {waStatus && (
                <div className="hint" style={{ marginBottom: 12 }}>
                  Webhook: {waStatus.webhook_url} · WA conversations: {waStatus.whatsapp_conversations} ·
                  processed events: {waStatus.processed_webhook_events}
                  {waStatus.profile_mismatch && (
                    <div style={{ color: "var(--warn, #b45309)", marginTop: 6 }}>
                      ⚠ Number is on <strong>{waStatus.number_profile_name}</strong> ({waStatus.number_profile_webhook || "wrong profile"}) —
                      inbound messages are NOT reaching this app. Click <strong>Auto-detect from Telnyx</strong>.
                    </div>
                  )}
                  {(waStatus.messaging_profiles || []).length > 0 && (
                    <div style={{ marginTop: 8, fontSize: "0.9em" }}>
                      <strong>menasim profile (WA 2-99):</strong>{" "}
                      {(waStatus.messaging_profiles[0].webhook_url || "no webhook")}
                      {waStatus.messaging_profiles[0].id === waStatus.number_profile_id
                        ? " · number assigned here ✓"
                        : waStatus.number_profile_name
                          ? ` · number is on ${waStatus.number_profile_name} instead`
                          : ""}
                    </div>
                  )}
                  {waStatus.suggested_waba_id && (
                    <div style={{ marginTop: 6 }}>Suggested WABA: {waStatus.suggested_waba_id}</div>
                  )}
                  {(waStatus.warnings || []).map((w) => (
                    <div key={w} style={{ color: "var(--warn, #b45309)", marginTop: 6 }}>⚠ {w}</div>
                  ))}
                  {waStatus.webhook_public_key_set && (
                    <div style={{ marginTop: 6 }}>
                      Webhook signature verification is ON — if inbound messages never arrive, clear the
                      webhook public key or paste the correct key from Telnyx → Messaging → Security.
                    </div>
                  )}
                  {(waStatus.recent_webhooks || []).length > 0 && (
                    <div style={{ marginTop: 10 }}>
                      <strong>Recent Telnyx webhooks</strong>
                      <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: "0.9em" }}>
                        {waStatus.recent_webhooks.map((w) => (
                          <li key={w.id}>
                            {w.created_at?.slice(11, 19)} · {w.status} · {w.sender || w.event_type}
                            {w.detail ? ` — ${w.detail.slice(0, 80)}` : ""}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
              <Field label="Test phone number" hint="Your mobile number to receive test messages and simulate inbound chats.">
                <input placeholder="+447..." value={testPhone} onChange={(e) => setTestPhone(e.target.value)} />
              </Field>
              <div className="field-actions" style={{ flexWrap: "wrap", gap: 8 }}>
                <button className="btn secondary" disabled={!!waBusy} onClick={() => autoConfigureWhatsapp()}>
                  {waBusy === "auto" ? "Detecting…" : "Auto-detect from Telnyx"}
                </button>
                <button className="btn secondary" disabled={!!waBusy} onClick={() => runWhatsappTest("connect")}>
                  {waBusy === "connect" ? "Testing…" : "Test connectivity"}
                </button>
                <button className="btn secondary" disabled={!!waBusy} onClick={() => runWhatsappTest("inbound")}>
                  {waBusy === "inbound" ? "Running…" : "Test agent (WA channel)"}
                </button>
                <button className="btn secondary" disabled={!!waBusy} onClick={() => runWhatsappTest("send")}>
                  {waBusy === "send" ? "Sending…" : "Send test message"}
                </button>
                <button className="btn secondary" disabled={!!waBusy} onClick={() => runWhatsappTest("full")}>
                  {waBusy === "full" ? "Running…" : "Test full round-trip"}
                </button>
                <ConnStatus state={conn.whatsapp || {}} />
                <button className="btn primary" disabled={saving === "whatsapp"} onClick={() => save("whatsapp")}>
                  {saving === "whatsapp" ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>
          )}

          {tab === "wordpress" && (
            <div className="card">
              <h3><Icon name="wordpress" /> WordPress / WooCommerce API</h3>
              <div className="desc">Connect to your WordPress site for customer orders and eSIM order data.</div>
              <Field label="Site base URL" hint="Site root or /wp-json — both work, e.g. https://menasim.com">
                <input placeholder="https://menasim.com" value={g.wordpress.wc_base_url}
                  onChange={(e) => setField("wordpress", "wc_base_url", e.target.value)} />
              </Field>
              <div className="row2col">
                <Field label="Consumer Key">
                  <input placeholder="ck_..." value={g.wordpress.wc_consumer_key}
                    onChange={(e) => setField("wordpress", "wc_consumer_key", e.target.value)} />
                </Field>
                <Field label="Consumer Secret">
                  <input type="password" placeholder="cs_..." value={g.wordpress.wc_consumer_secret}
                    onChange={(e) => setField("wordpress", "wc_consumer_secret", e.target.value)} />
                </Field>
              </div>
              <div className="row2col">
                <Field label="eSIM ICCID meta keys" hint="Comma-separated order meta keys">
                  <input value={g.wordpress.wc_esim_iccid_meta}
                    onChange={(e) => setField("wordpress", "wc_esim_iccid_meta", e.target.value)} />
                </Field>
                <Field label="eSIM QR / activation meta keys">
                  <input value={g.wordpress.wc_esim_qr_meta}
                    onChange={(e) => setField("wordpress", "wc_esim_qr_meta", e.target.value)} />
                </Field>
              </div>
              <Field label="eSIM status meta keys">
                <input value={g.wordpress.wc_esim_status_meta}
                  onChange={(e) => setField("wordpress", "wc_esim_status_meta", e.target.value)} />
              </Field>
              <div className="field-actions">
                <button className="btn secondary" onClick={() => test("wordpress")}>Test connection</button>
                <ConnStatus state={conn.wordpress || {}} />
                <button className="btn primary" disabled={saving === "wordpress"} onClick={() => save("wordpress")}>
                  {saving === "wordpress" ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>
          )}

          {tab === "agent" && (
            <AgentTab g={g} setField={setField} save={save} saving={saving} toast={toast} />
          )}

          {tab === "voice" && (
            <div className="card">
              <h3><Icon name="mic" /> Voice notes (DeepInfra Whisper)</h3>
              <div className="desc">
                Lets customers send voice notes. Audio is transcribed with Whisper (auto-detects
                Arabic / English) and answered like a normal message. Install ffmpeg on the server.
              </div>
              <Field label="DeepInfra API key">
                <input type="password" placeholder="di-..." value={g.voice.deepinfra_api_key}
                  onChange={(e) => setField("voice", "deepinfra_api_key", e.target.value)} />
              </Field>
              <div className="row2col">
                <Field label="Whisper model">
                  <input value={g.voice.deepinfra_whisper_model}
                    onChange={(e) => setField("voice", "deepinfra_whisper_model", e.target.value)} />
                </Field>
                <Field label="Voice notes enabled" hint="Turn the record button + transcription on/off.">
                  <select value={g.voice.voice_enabled}
                    onChange={(e) => setField("voice", "voice_enabled", e.target.value)}>
                    <option value="true">Enabled</option>
                    <option value="false">Disabled</option>
                  </select>
                </Field>
              </div>
              <div className="field-actions">
                <button className="btn secondary" onClick={() => test("voice")}>Test connection</button>
                <ConnStatus state={conn.voice || {}} />
                <button className="btn primary" disabled={saving === "voice"} onClick={() => save("voice")}>
                  {saving === "voice" ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>
          )}

          {tab === "smtp" && (
            <div className="card">
              <h3><Icon name="mail" /> Escalation email alerts</h3>
              <div className="desc">SMTP details used to email your team when the agent escalates or logs a refund.</div>
              <div className="row2col">
                <Field label="SMTP host">
                  <input placeholder="smtp.gmail.com" value={g.smtp.smtp_host}
                    onChange={(e) => setField("smtp", "smtp_host", e.target.value)} />
                </Field>
                <Field label="SMTP port">
                  <input value={g.smtp.smtp_port} onChange={(e) => setField("smtp", "smtp_port", e.target.value)} />
                </Field>
              </div>
              <div className="row2col">
                <Field label="SMTP user">
                  <input value={g.smtp.smtp_user} onChange={(e) => setField("smtp", "smtp_user", e.target.value)} />
                </Field>
                <Field label="SMTP password">
                  <input type="password" value={g.smtp.smtp_pass}
                    onChange={(e) => setField("smtp", "smtp_pass", e.target.value)} />
                </Field>
              </div>
              <div className="row2col">
                <Field label="From address">
                  <input value={g.smtp.smtp_from} onChange={(e) => setField("smtp", "smtp_from", e.target.value)} />
                </Field>
                <Field label="Alert recipient(s)">
                  <input placeholder="support@menasim.com" value={g.smtp.alert_email_to}
                    onChange={(e) => setField("smtp", "alert_email_to", e.target.value)} />
                </Field>
              </div>
              <div className="field-actions">
                <div />
                <button className="btn primary" disabled={saving === "smtp"} onClick={() => save("smtp")}>
                  {saving === "smtp" ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AgentTab({ g, setField, save, saving, toast }) {
  const [kb, setKb] = useState(null);
  const [busy, setBusy] = useState(false);
  const [lang, setLang] = useState("en");
  const fileRef = useRef(null);

  // Manual FAQ / editing
  const [editing, setEditing] = useState(null); // { source, body } | null
  const [newFaq, setNewFaq] = useState({ title: "", language: "en", body: "" });

  // WhatsApp import
  const [wa, setWa] = useState({ supportName: "", language: "auto", distill: true });
  const waRef = useRef(null);

  async function loadKb() {
    try {
      setKb(await api.listKb());
    } catch (e) {
      /* ignore */
    }
  }
  useEffect(() => {
    loadKb();
  }, []);

  async function reindex() {
    setBusy(true);
    try {
      const res = await api.reindexKb();
      toast(`Reindexed ${res.documents} docs / ${res.chunks} chunks.`);
      await loadKb();
    } catch (e) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function upload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const res = await api.uploadKb(file, lang);
      toast(`Uploaded ${res.saved}. KB now ${res.chunks} chunks.`);
      await loadKb();
    } catch (e2) {
      toast(e2.message, true);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function openEditor(source) {
    setBusy(true);
    try {
      const res = await api.getKbDoc(source);
      setEditing({ source, body: res.body });
    } catch (e) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function saveEditor() {
    setBusy(true);
    try {
      const res = await api.saveKbDoc(editing.source, editing.body);
      toast(`Saved. KB now ${res.chunks} chunks.`);
      setEditing(null);
      await loadKb();
    } catch (e) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function removeDoc(source) {
    if (!window.confirm(`Delete ${source}? This cannot be undone.`)) return;
    setBusy(true);
    try {
      const res = await api.deleteKbDoc(source);
      toast(`Deleted. KB now ${res.chunks} chunks.`);
      if (editing && editing.source === source) setEditing(null);
      await loadKb();
    } catch (e) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function createFaq() {
    if (!newFaq.title.trim()) {
      toast("Give the FAQ a title.", true);
      return;
    }
    setBusy(true);
    try {
      const res = await api.createKbDoc(newFaq.title, newFaq.language, newFaq.body);
      toast(`Created ${res.source}. KB now ${res.chunks} chunks.`);
      setNewFaq({ title: "", language: "en", body: "" });
      await loadKb();
    } catch (e) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function importWhatsapp(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const res = await api.importWhatsapp(file, wa.supportName, wa.language, wa.distill);
      if (res.written) {
        toast(`Imported ${res.messages} messages → ${res.source}. KB now ${res.chunks} chunks.`);
      } else {
        toast(`Nothing imported (${res.reason || "no usable messages"}).`, true);
      }
      await loadKb();
    } catch (e2) {
      toast(e2.message, true);
    } finally {
      setBusy(false);
      if (waRef.current) waRef.current.value = "";
    }
  }

  return (
    <>
      <div className="card">
        <h3>Agent profile</h3>
        <div className="desc">How the agent introduces itself and behaves in conversation.</div>
        <div className="row2col">
          <Field label="Agent name">
            <input value={g.agent.agent_name} onChange={(e) => setField("agent", "agent_name", e.target.value)} />
          </Field>
          <Field label="Tone / persona">
            <select value={g.agent.agent_tone} onChange={(e) => setField("agent", "agent_tone", e.target.value)}>
              <option>Friendly & concise</option>
              <option>Formal & precise</option>
              <option>Casual & upbeat</option>
            </select>
          </Field>
        </div>
        <Field label="Extra system instructions" hint="Appended to the built-in bilingual eSIM support prompt.">
          <textarea value={g.agent.agent_system_instructions}
            onChange={(e) => setField("agent", "agent_system_instructions", e.target.value)} />
        </Field>
        <div className="row2col">
          <Field label="Max tool iterations">
            <input value={g.agent.agent_max_tool_iters}
              onChange={(e) => setField("agent", "agent_max_tool_iters", e.target.value)} />
          </Field>
          <Field label="Rate limit (msgs / minute per user)">
            <input value={g.agent.rate_limit_per_minute}
              onChange={(e) => setField("agent", "rate_limit_per_minute", e.target.value)} />
          </Field>
        </div>
        <div className="field-actions">
          <div />
          <button className="btn primary" disabled={saving === "agent"} onClick={() => save("agent")}>
            {saving === "agent" ? "Saving…" : "Save agent configuration"}
          </button>
        </div>
      </div>

      <div className="card">
        <h3><Icon name="book" /> Knowledge base</h3>
        <div className="desc">Markdown documents the agent references (RAG). Stored under kb_docs/&#123;en,ar&#125;.</div>
        <div className="row2col">
          <Field label="Language for upload">
            <select value={lang} onChange={(e) => setLang(e.target.value)}>
              <option value="en">English</option>
              <option value="ar">Arabic</option>
            </select>
          </Field>
          <Field label="Upload a .md / .txt file">
            <input ref={fileRef} type="file" accept=".md,.txt,text/plain,text/markdown" disabled={busy} onChange={upload} />
          </Field>
        </div>
        <div className="field-actions" style={{ marginTop: 4 }}>
          <button className="btn secondary" disabled={busy} onClick={reindex}>
            {busy ? "Working…" : "Rebuild index"}
          </button>
          <div className="conn-status"><span>{kb ? `${kb.chunks} chunks indexed` : "…"}</span></div>
          <div />
        </div>
        {kb && kb.documents.length > 0 && (
          <ul className="kb-list">
            {kb.documents.map((d) => (
              <li key={d.id}>
                <span>{d.title} <span className="muted">({d.language}) · {d.chunk_count} chunks</span></span>
                <span style={{ display: "flex", gap: 6 }}>
                  <button className="btn secondary" disabled={busy} onClick={() => openEditor(d.source)}>Edit</button>
                  <button className="btn secondary" disabled={busy} onClick={() => removeDoc(d.source)}>Delete</button>
                </span>
              </li>
            ))}
          </ul>
        )}

        {editing && (
          <div className="field" style={{ marginTop: 12 }}>
            <label>Editing {editing.source}</label>
            <textarea
              style={{ minHeight: 220, fontFamily: "monospace" }}
              value={editing.body}
              onChange={(e) => setEditing({ ...editing, body: e.target.value })}
            />
            <div className="field-actions" style={{ marginTop: 8 }}>
              <button className="btn secondary" onClick={() => setEditing(null)}>Cancel</button>
              <div />
              <button className="btn primary" disabled={busy} onClick={saveEditor}>
                {busy ? "Saving…" : "Save document"}
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <h3><Icon name="book" /> New FAQ</h3>
        <div className="desc">Paste an answer and the agent will use it (RAG). Saved as a Markdown doc under kb_docs.</div>
        <div className="row2col">
          <Field label="Title / question">
            <input value={newFaq.title} placeholder="e.g. How to install eSIM on iPhone"
              onChange={(e) => setNewFaq({ ...newFaq, title: e.target.value })} />
          </Field>
          <Field label="Language">
            <select value={newFaq.language} onChange={(e) => setNewFaq({ ...newFaq, language: e.target.value })}>
              <option value="en">English</option>
              <option value="ar">Arabic</option>
            </select>
          </Field>
        </div>
        <Field label="Answer / content" hint="Plain text or Markdown.">
          <textarea style={{ minHeight: 140 }} value={newFaq.body}
            onChange={(e) => setNewFaq({ ...newFaq, body: e.target.value })} />
        </Field>
        <div className="field-actions">
          <div />
          <button className="btn primary" disabled={busy} onClick={createFaq}>
            {busy ? "Saving…" : "Add FAQ"}
          </button>
        </div>
      </div>

      <div className="card">
        <h3><Icon name="whatsapp" /> Import WhatsApp history</h3>
        <div className="desc">
          Upload an exported WhatsApp chat (.txt). The agent learns reusable support points via RAG.
          With AI distill on, entries are summarised and personal data (names, numbers, orders) is stripped.
        </div>
        <div className="row2col">
          <Field label="Your support name in the chat" hint="Used to tell agent vs. customer apart, e.g. 'Menasim'">
            <input value={wa.supportName} placeholder="Menasim Support"
              onChange={(e) => setWa({ ...wa, supportName: e.target.value })} />
          </Field>
          <Field label="Language">
            <select value={wa.language} onChange={(e) => setWa({ ...wa, language: e.target.value })}>
              <option value="auto">Auto-detect</option>
              <option value="en">English</option>
              <option value="ar">Arabic</option>
            </select>
          </Field>
        </div>
        <div className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input id="wa-distill" type="checkbox" checked={wa.distill}
            onChange={(e) => setWa({ ...wa, distill: e.target.checked })} />
          <label htmlFor="wa-distill" style={{ margin: 0 }}>Distill with AI &amp; strip personal data (recommended)</label>
        </div>
        <Field label="WhatsApp export (.txt)">
          <input ref={waRef} type="file" accept=".txt,text/plain" disabled={busy} onChange={importWhatsapp} />
        </Field>
      </div>

      <div className="card">
        <h3>Skills</h3>
        <div className="desc">Actions the agent can take during a conversation (built-in tools).</div>
        <div>
          {BUILTIN_SKILLS.map((s) => (
            <span className="skill-tag" key={s}>{s}</span>
          ))}
        </div>
      </div>
    </>
  );
}
