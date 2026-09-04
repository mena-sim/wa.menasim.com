const TOKEN_KEY = "menasim_admin_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

async function request(method, path, body, isForm = false) {
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let payload;
  if (isForm) {
    payload = body; // FormData
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const resp = await fetch(`/api${path}`, { method, headers, body: payload });
  if (resp.status === 401) {
    setToken("");
    onUnauthorized();
    throw new Error("Unauthorized");
  }
  const text = await resp.text();
  const data = text ? JSON.parse(text) : {};
  if (!resp.ok) {
    throw new Error(data.detail || data.message || `HTTP ${resp.status}`);
  }
  return data;
}

export const api = {
  login: (password) => request("POST", "/admin/login", { password }),
  logout: () => request("POST", "/admin/logout"),
  me: () => request("GET", "/admin/me"),

  getConfig: () => request("GET", "/admin/config"),
  saveConfig: (group, values) => request("PUT", `/admin/config/${group}`, { values }),
  testConfig: (group) => request("POST", `/admin/config/test/${group}`),
  testSmtpSend: (to = "") => request("POST", "/admin/smtp/test-send", { to }),

  getWhatsappStatus: () => request("GET", "/admin/whatsapp/status"),
  autoConfigureWhatsapp: () => request("POST", "/admin/whatsapp/auto-configure"),
  testWhatsappSend: (to, text) => request("POST", "/admin/whatsapp/test-send", { to, text }),
  testWhatsappInbound: (from_number, text, send_reply = false) =>
    request("POST", "/admin/whatsapp/test-inbound", { from_number, text, send_reply }),

  listTwilioNumbers: () => request("GET", "/admin/twilio/numbers"),
  configureTwilioSms: (sid, phone_number = "") =>
    request("POST", "/admin/twilio/configure-sms", { sid, phone_number }),
  testSmsSend: (to, text) => request("POST", "/admin/sms/test-send", { to, text }),

  listConversations: (filter = "all", q = "", channel = "all") =>
    request(
      "GET",
      `/admin/conversations?filter=${encodeURIComponent(filter)}&channel=${encodeURIComponent(channel)}&q=${encodeURIComponent(q)}`
    ),
  getConversation: (id) => request("GET", `/admin/conversations/${id}`),
  setHandover: (id, handed_over) =>
    request("POST", `/admin/conversations/${id}/handover`, { handed_over }),
  reply: (id, text) => request("POST", `/admin/conversations/${id}/reply`, { text }),
  closeConversation: (id) => request("POST", `/admin/conversations/${id}/close`),

  listKb: () => request("GET", "/admin/kb"),
  reindexKb: () => request("POST", "/admin/kb/reindex"),
  uploadKb: (file, language) => {
    const fd = new FormData();
    fd.append("file", file);
    return request("POST", `/admin/kb/upload?language=${language}`, fd, true);
  },
  getKbDoc: (source) => request("GET", `/admin/kb/doc?source=${encodeURIComponent(source)}`),
  createKbDoc: (title, language, body) =>
    request("POST", "/admin/kb/doc", { title, language, body }),
  saveKbDoc: (source, body) => request("PUT", "/admin/kb/doc", { source, body }),
  deleteKbDoc: (source) => request("DELETE", `/admin/kb/doc?source=${encodeURIComponent(source)}`),
  importWhatsapp: (file, supportName, language, distill) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("support_name", supportName || "");
    fd.append("language", language || "auto");
    fd.append("distill_with_ai", distill ? "true" : "false");
    return request("POST", "/admin/kb/import-whatsapp", fd, true);
  },
  saveConversationToKb: (id) => request("POST", `/admin/conversations/${id}/save-to-kb`),
};
