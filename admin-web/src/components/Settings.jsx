import { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { api } from "../api.js";

const TABS = [
  { key: "deepseek", label: "DeepSeek LLM", icon: "brain" },
  { key: "whatsapp", label: "WhatsApp", icon: "whatsapp" },
  { key: "telnyx", label: "Telnyx", icon: "telnyx" },
  { key: "twilio", label: "Twilio", icon: "telnyx" },
  { key: "meta", label: "Meta (direct)", icon: "brain" },
  { key: "wordpress", label: "WordPress API", icon: "wordpress" },
  { key: "agent", label: "Agent & KB", icon: "book" },
  { key: "voice", label: "Voice notes", icon: "mic" },
  { key: "smtp", label: "Email Alerts", icon: "mail" },
];

const WA_PROVIDERS = [
  { value: "telnyx", label: "Telnyx" },
  { value: "twilio", label: "Twilio" },
  { value: "meta", label: "Meta Cloud API (direct)" },
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
  const [smtpBusy, setSmtpBusy] = useState("");
  const [twilioNumbers, setTwilioNumbers] = useState([]);
  const [twilioBusy, setTwilioBusy] = useState("");
  const [twilioMessages, setTwilioMessages] = useState([]);
  const [twilioMsgHint, setTwilioMsgHint] = useState("");

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
    if (["whatsapp", "telnyx", "twilio", "meta"].includes(tab)) loadWhatsappStatus();
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

  async function runSmtpTest(kind) {
    if (kind === "connect") {
      await test("smtp");
      return;
    }
    setSmtpBusy(kind);
    try {
      const res = await api.testSmtpSend();
      toast(res.message, !res.ok);
      if (res.ok) {
        setConn((c) => ({
          ...c,
          smtp: { status: "ok", msg: res.message },
        }));
      }
    } catch (e) {
      toast(e.message, true);
    } finally {
      setSmtpBusy("");
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
    const provider = groups?.whatsapp?.whatsapp_provider || status.whatsapp_provider || "telnyx";
    const fromNumber =
      provider === "twilio"
        ? groups?.twilio?.twilio_whatsapp_from
        : provider === "meta"
          ? groups?.meta?.meta_whatsapp_from || groups?.whatsapp?.telnyx_whatsapp_from
          : groups?.whatsapp?.telnyx_whatsapp_from;
    const phone = (testPhone || fromNumber || "").trim();
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

  async function loadTwilioNumbers(quiet = false) {
    setTwilioBusy("list");
    try {
      const res = await api.listTwilioNumbers();
      setTwilioNumbers(res.numbers || []);
      if (!quiet) toast(res.message, !res.ok);
    } catch (e) {
      toast(e.message, true);
    } finally {
      setTwilioBusy("");
    }
  }

  async function loadTwilioMessages() {
    setTwilioBusy("messages");
    try {
      const to = (groups?.twilio?.twilio_sms_from || groups?.twilio?.twilio_whatsapp_from || "").trim();
      const res = await api.listTwilioMessages(to);
      setTwilioMessages(res.messages || []);
      setTwilioMsgHint(res.hint || "");
      toast(res.message, !res.ok);
    } catch (e) {
      toast(e.message, true);
    } finally {
      setTwilioBusy("");
    }
  }

  async function configureTwilioSms(sid, phoneNumber) {
    setTwilioBusy(sid || phoneNumber || "configure");
    try {
      const res = await api.configureTwilioSms(sid, phoneNumber);
      toast(res.message, !res.ok);
      if (res.ok) {
        if (res.phone_number) setField("twilio", "twilio_sms_from", res.phone_number);
        setField("twilio", "twilio_sms_enabled", "true");
        await load();
        await loadTwilioNumbers(true);
      }
    } catch (e) {
      toast(e.message, true);
    } finally {
      setTwilioBusy("");
    }
  }

  async function runSmsTestSend() {
    const phone = (testPhone || groups?.twilio?.twilio_sms_from || "").trim();
    if (!phone) {
      toast("Enter a test phone number (your mobile) to receive the SMS.", true);
      return;
    }
    setTwilioBusy("send");
    try {
      const res = await api.testSmsSend(
        phone,
        "Test from menasim support. If you see this, Twilio SMS is working."
      );
      toast(res.message, !res.ok);
    } catch (e) {
      toast(e.message, true);
    } finally {
      setTwilioBusy("");
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
  const activeProvider = g.whatsapp?.whatsapp_provider || status.whatsapp_provider || "telnyx";
  const webhookUrls = status.webhook_urls || {};
  const activeWebhook = webhookUrls[activeProvider] || status.webhook_url || "";
  const smsWebhook =
    status.sms_webhook_url || webhookUrls.sms || `${status.public_base_url || ""}/twilio/webhooks/sms`;

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
              <Field label="Webhook URL (configure this in the Telnyx portal)" hint="Used when WhatsApp provider is Telnyx.">
                <input readOnly value={webhookUrls.telnyx || status.webhook_url || "https://wa.menasim.com/telnyx/webhooks/messages"} />
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
              <h3><Icon name="whatsapp" /> WhatsApp channel</h3>
              <div className="desc">
                Choose how customers reach the AI agent on WhatsApp. Only one provider is active at a time.
              </div>
              <Field label="Active provider" hint="Inbound webhooks and outbound replies use this provider.">
                <select
                  value={g.whatsapp.whatsapp_provider || "telnyx"}
                  onChange={(e) => setField("whatsapp", "whatsapp_provider", e.target.value)}
                >
                  {WA_PROVIDERS.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </Field>
              <Field label="Active webhook URL" hint={`Configure this URL in ${activeProvider === "meta" ? "Meta Developer Console" : activeProvider === "twilio" ? "Twilio Console" : "Telnyx portal"}.`}>
                <input readOnly value={activeWebhook} />
              </Field>
              <div className="row2col">
                <Field label="Display name">
                  <input value={g.whatsapp.whatsapp_display_name}
                    onChange={(e) => setField("whatsapp", "whatsapp_display_name", e.target.value)} />
                </Field>
                <Field label="Business account ID (WABA)" hint="Meta WhatsApp Business account ID — reference for Meta/Telnyx.">
                  <input placeholder="1339285631627922" value={g.whatsapp.whatsapp_business_id}
                    onChange={(e) => setField("whatsapp", "whatsapp_business_id", e.target.value)} />
                </Field>
              </div>
              {activeProvider === "telnyx" && (
                <Field label="Sender number (Telnyx)" hint="E.164, e.g. +447822002099">
                  <input placeholder="+447822002099" value={g.whatsapp.telnyx_whatsapp_from}
                    onChange={(e) => setField("whatsapp", "telnyx_whatsapp_from", e.target.value)} />
                </Field>
              )}
              {activeProvider === "twilio" && (
                <>
                <Field label="Sender number (Twilio)" hint="Set on the Twilio tab.">
                  <input readOnly value={g.twilio?.twilio_whatsapp_from || "(set on Twilio tab)"} />
                </Field>
                <div className="hint" style={{ marginBottom: 12 }}>
                  Inbound SMS is configured separately on the <strong>Twilio</strong> tab
                  (load numbers → Use for SMS). SMS webhook: {smsWebhook}
                </div>
                </>
              )}
              {activeProvider === "meta" && (
                <Field label="Sender number (Meta)" hint="Set on the Meta tab.">
                  <input readOnly value={g.meta?.meta_whatsapp_from || "(set on Meta tab)"} />
                </Field>
              )}
              {waStatus && (
                <div className="hint" style={{ marginBottom: 12 }}>
                  Provider: <strong>{waStatus.provider || activeProvider}</strong> ·
                  Webhook: {waStatus.webhook_url || activeWebhook} ·
                  WA conversations: {waStatus.whatsapp_conversations ?? "—"}
                  {activeProvider === "telnyx" && waStatus.profile_mismatch && (
                    <div style={{ color: "var(--warn, #b45309)", marginTop: 6 }}>
                      ⚠ Wrong Telnyx profile — use <strong>Auto-detect from Telnyx</strong>.
                    </div>
                  )}
                  {(waStatus.warnings || []).map((w) => (
                    <div key={w} style={{ color: "var(--warn, #b45309)", marginTop: 6 }}>⚠ {w}</div>
                  ))}
                </div>
              )}
              <Field label="Test phone number" hint="Your mobile number to receive test messages and simulate inbound chats.">
                <input placeholder="+447..." value={testPhone} onChange={(e) => setTestPhone(e.target.value)} />
              </Field>
              <div className="field-actions" style={{ flexWrap: "wrap", gap: 8 }}>
                {activeProvider === "telnyx" && (
                  <button className="btn secondary" disabled={!!waBusy} onClick={() => autoConfigureWhatsapp()}>
                    {waBusy === "auto" ? "Detecting…" : "Auto-detect from Telnyx"}
                  </button>
                )}
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

          {tab === "twilio" && (
            <>
            <div className="card">
              <h3><Icon name="telnyx" /> Twilio account</h3>
              <div className="desc">
                Use the same Twilio account for WhatsApp and/or inbound SMS. SMS works even if
                the WhatsApp provider stays Telnyx or Meta.
              </div>
              <ol className="setup-steps">
                <li>In Twilio Console open <strong>Account → API keys &amp; tokens</strong> and copy the Account SID and Auth Token.</li>
                <li>Buy or port a mobile number under <strong>Phone Numbers → Manage → Buy a number</strong>. Enable <strong>SMS</strong> (MMS optional).</li>
                <li>Save credentials below, then click <strong>Load numbers</strong> and <strong>Use for SMS</strong> on the number. That points Twilio’s inbound SMS webhook at this app.</li>
                <li>Text that number from your phone — the chat appears in Conversations as channel <strong>sms</strong>.</li>
                <li>Optional WhatsApp: set the WhatsApp sender, then choose <strong>Twilio</strong> on the WhatsApp tab.</li>
              </ol>
              <Field label="Account SID">
                <input placeholder="AC..." value={g.twilio.twilio_account_sid}
                  onChange={(e) => setField("twilio", "twilio_account_sid", e.target.value)} />
              </Field>
              <Field label="Auth token">
                <input type="password" placeholder="auth token" value={g.twilio.twilio_auth_token}
                  onChange={(e) => setField("twilio", "twilio_auth_token", e.target.value)} />
              </Field>
              <div className="field-actions">
                <button className="btn secondary" onClick={() => test("twilio")}>Test connection</button>
                <ConnStatus state={conn.twilio || {}} />
                <button className="btn primary" disabled={saving === "twilio"} onClick={() => save("twilio")}>
                  {saving === "twilio" ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>

            <div className="card">
              <h3>Receive SMS</h3>
              <div className="desc">
                Point a Twilio mobile number at this webhook so customers can text the agent.
                Meta WhatsApp verification codes also arrive as SMS to this number — they will not
                show on your personal phone. Click <strong>Load recent SMS</strong> after Meta sends the code.
                If the number is in a Messaging Service, set that service’s inbound URL to the same webhook.
              </div>
              <Field label="Enable inbound SMS">
                <select value={g.twilio?.twilio_sms_enabled || "false"}
                  onChange={(e) => setField("twilio", "twilio_sms_enabled", e.target.value)}>
                  <option value="true">Enabled</option>
                  <option value="false">Disabled</option>
                </select>
              </Field>
              <Field label="SMS number" hint="E.164 Twilio number that receives texts. Can be the same as the WhatsApp number.">
                <input placeholder="+447..." value={g.twilio?.twilio_sms_from || ""}
                  onChange={(e) => setField("twilio", "twilio_sms_from", e.target.value)} />
              </Field>
              <Field label="SMS webhook URL" hint="Twilio Console → Phone Numbers → the number → Messaging → “A message comes in” (HTTP POST).">
                <input readOnly value={smsWebhook} />
              </Field>
              <div className="field-actions" style={{ marginTop: 0 }}>
                <button className="btn secondary" disabled={!!twilioBusy} onClick={loadTwilioNumbers}>
                  {twilioBusy === "list" ? "Loading…" : "Load numbers from Twilio"}
                </button>
                <button className="btn secondary" disabled={!!twilioBusy} onClick={loadTwilioMessages}>
                  {twilioBusy === "messages" ? "Loading…" : "Load recent SMS"}
                </button>
                <button className="btn secondary" disabled={!!twilioBusy} onClick={runSmsTestSend}>
                  {twilioBusy === "send" ? "Sending…" : "Send test SMS"}
                </button>
                <button className="btn primary" disabled={saving === "twilio"} onClick={() => save("twilio")}>
                  {saving === "twilio" ? "Saving…" : "Save SMS settings"}
                </button>
              </div>
              {twilioNumbers.length > 0 && (
                <ul className="number-list">
                  {twilioNumbers.map((n) => (
                    <li key={n.sid}>
                      <div>
                        <strong>{n.phone_number}</strong>
                        <span className="muted">
                          {" "}{n.friendly_name && n.friendly_name !== n.phone_number ? `· ${n.friendly_name} ` : ""}
                          {n.capabilities?.sms ? "SMS" : "no SMS"}
                          {n.capabilities?.mms ? " · MMS" : ""}
                          {n.sms_configured ? " · webhook OK" : n.sms_url ? ` · webhook: ${n.sms_url}` : " · webhook not set"}
                        </span>
                      </div>
                      <button
                        className="btn secondary"
                        disabled={!!twilioBusy || !n.capabilities?.sms}
                        onClick={() => configureTwilioSms(n.sid, n.phone_number)}
                      >
                        {twilioBusy === n.sid ? "Setting…" : "Use for SMS"}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {twilioMessages.length > 0 && (
                <ul className="number-list">
                  {twilioMessages.map((m) => (
                    <li key={m.sid || `${m.date_sent}-${m.from}-${m.body}`}>
                      <div>
                        <strong>{m.inbound ? "IN" : "OUT"}</strong>
                        <span className="muted">
                          {" "}{m.from} → {m.to} · {m.status}
                          {m.date_sent ? ` · ${m.date_sent}` : ""}
                        </span>
                        <div style={{ marginTop: 4 }}>{m.body || "(no body)"}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {twilioMsgHint && (
                <div className="hint" style={{ color: "var(--warn, #b45309)", marginBottom: 12 }}>
                  {twilioMsgHint}
                </div>
              )}
              <Field label="Test phone number" hint="Your personal mobile — used only for Send test SMS.">
                <input placeholder="+447..." value={testPhone} onChange={(e) => setTestPhone(e.target.value)} />
              </Field>
            </div>

            <div className="card">
              <h3>Twilio WhatsApp</h3>
              <div className="desc">
                Connect a WhatsApp-enabled Twilio sender. Set provider to <strong>Twilio</strong> on the WhatsApp tab to activate outbound WhatsApp.
              </div>
              <Field label="WhatsApp sender number" hint="E.164, e.g. +14155238886 (sandbox) or your approved WA number">
                <input placeholder="+447..." value={g.twilio.twilio_whatsapp_from}
                  onChange={(e) => setField("twilio", "twilio_whatsapp_from", e.target.value)} />
              </Field>
              <Field label="WhatsApp webhook URL" hint="Twilio Console → Messaging → WhatsApp senders / sandbox → when a message comes in.">
                <input readOnly value={webhookUrls.twilio || `${status.public_base_url || ""}/twilio/webhooks/whatsapp`} />
              </Field>
              <div className="field-actions">
                <div />
                <button className="btn primary" disabled={saving === "twilio"} onClick={() => save("twilio")}>
                  {saving === "twilio" ? "Saving…" : "Save WhatsApp sender"}
                </button>
              </div>
            </div>
            </>
          )}

          {tab === "meta" && (
            <div className="card">
              <h3><Icon name="brain" /> Meta WhatsApp Cloud API (direct)</h3>
              <div className="desc">
                Connect WhatsApp directly via Meta — no Telnyx middleman. Set provider to <strong>Meta Cloud API</strong> on the WhatsApp tab.
                When Meta asks to verify the phone number it sends an <strong>SMS code to the Twilio number</strong>, not to your mobile.
                Open the <strong>Twilio</strong> tab → <strong>Load recent SMS</strong> and paste the code back into Meta.
                Twilio trial accounts cannot receive that SMS until you upgrade (trial only accepts SMS from verified numbers).
              </div>
              <Field label="Permanent access token">
                <input type="password" placeholder="EAA..." value={g.meta.meta_whatsapp_token}
                  onChange={(e) => setField("meta", "meta_whatsapp_token", e.target.value)} />
              </Field>
              <div className="row2col">
                <Field label="Phone number ID" hint="From Meta Developer Console → WhatsApp → API Setup">
                  <input placeholder="1234567890" value={g.meta.meta_phone_number_id}
                    onChange={(e) => setField("meta", "meta_phone_number_id", e.target.value)} />
                </Field>
                <Field label="Display phone number (E.164)" hint="For reference in admin UI">
                  <input placeholder="+447822002099" value={g.meta.meta_whatsapp_from}
                    onChange={(e) => setField("meta", "meta_whatsapp_from", e.target.value)} />
                </Field>
              </div>
              <div className="row2col">
                <Field label="WABA ID">
                  <input placeholder="1339285631627922" value={g.meta.meta_waba_id}
                    onChange={(e) => setField("meta", "meta_waba_id", e.target.value)} />
                </Field>
                <Field label="Webhook verify token" hint="You choose this — same value in Meta webhook setup">
                  <input placeholder="menasim-verify-token" value={g.meta.meta_verify_token}
                    onChange={(e) => setField("meta", "meta_verify_token", e.target.value)} />
                </Field>
              </div>
              <Field label="App secret" hint="Used to verify X-Hub-Signature-256 on inbound webhooks">
                <input type="password" placeholder="app secret" value={g.meta.meta_app_secret}
                  onChange={(e) => setField("meta", "meta_app_secret", e.target.value)} />
              </Field>
              <Field label="Webhook URL (Meta Developer Console → WhatsApp → Configuration)">
                <input readOnly value={webhookUrls.meta || `${status.public_base_url || ""}/meta/webhooks/whatsapp`} />
              </Field>
              <div className="field-actions">
                <button className="btn secondary" onClick={() => test("meta")}>Test connection</button>
                <ConnStatus state={conn.meta || {}} />
                <button className="btn primary" disabled={saving === "meta"} onClick={() => save("meta")}>
                  {saving === "meta" ? "Saving…" : "Save changes"}
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
                Voice notes: Whisper transcribes audio (auto language). Screenshots: vision model
                reads phone settings images. Only images accepted on WhatsApp — not PDF/video/files.
                Install ffmpeg on the server for some audio formats.
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
                <Field label="Vision model (screenshots)">
                  <input value={g.voice.deepinfra_vision_model}
                    onChange={(e) => setField("voice", "deepinfra_vision_model", e.target.value)} />
                </Field>
              </div>
              <Field label="Voice notes enabled" hint="Turn voice transcription on/off (needs DeepInfra key).">
                <select value={g.voice.voice_enabled}
                  onChange={(e) => setField("voice", "voice_enabled", e.target.value)}>
                  <option value="true">Enabled</option>
                  <option value="false">Disabled</option>
                </select>
              </Field>
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
              <div className="desc">
                SMTP for escalation/refund alerts. menasim mail server: <strong>mail.menasim.com</strong>, port{" "}
                <strong>465</strong> (SSL).
              </div>
              <div className="row2col">
                <Field label="SMTP host" hint="e.g. mail.menasim.com">
                  <input placeholder="mail.menasim.com" value={g.smtp.smtp_host}
                    onChange={(e) => setField("smtp", "smtp_host", e.target.value)} />
                </Field>
                <Field label="SMTP port" hint="465 = SSL (outgoing). 587 = STARTTLS.">
                  <input placeholder="465" value={g.smtp.smtp_port}
                    onChange={(e) => setField("smtp", "smtp_port", e.target.value)} />
                </Field>
              </div>
              <div className="row2col">
                <Field label="SMTP user" hint="Usually your full email address">
                  <input placeholder="alerts@menasim.com" value={g.smtp.smtp_user}
                    onChange={(e) => setField("smtp", "smtp_user", e.target.value)} />
                </Field>
                <Field label="SMTP password">
                  <input type="password" value={g.smtp.smtp_pass}
                    onChange={(e) => setField("smtp", "smtp_pass", e.target.value)} />
                </Field>
              </div>
              <div className="row2col">
                <Field label="From address" hint="Must be allowed on your mail server">
                  <input placeholder="alerts@menasim.com" value={g.smtp.smtp_from}
                    onChange={(e) => setField("smtp", "smtp_from", e.target.value)} />
                </Field>
                <Field label="Alert recipient(s)">
                  <input placeholder="support@menasim.com" value={g.smtp.alert_email_to}
                    onChange={(e) => setField("smtp", "alert_email_to", e.target.value)} />
                </Field>
              </div>
              <div className="field-actions">
                <button className="btn secondary" onClick={() => runSmtpTest("connect")}>Test connection</button>
                <button
                  className="btn secondary"
                  disabled={smtpBusy === "send"}
                  onClick={() => runSmtpTest("send")}
                >
                  {smtpBusy === "send" ? "Sending…" : "Send test email"}
                </button>
                <ConnStatus state={conn.smtp || {}} />
                <button className="btn primary" disabled={saving === "smtp"} onClick={() => save("smtp")}>
                  {saving === "smtp" ? "Saving…" : "Save changes"}
                </button>
              </div>
              <div className="hint" style={{ marginTop: 8 }}>
                Save settings first, then test. The test email goes to the alert recipient above.
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
