import { useState } from "react";
import { api, setToken } from "../api.js";
import { LOGO } from "../icons.js";

export default function Login({ onLogin }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const { token } = await api.login(password);
      setToken(token);
      onLogin();
    } catch (err) {
      setError("Invalid password. Check ADMIN_PASSWORD in your .env.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-overlay">
      <form className="login-box" onSubmit={submit}>
        <div className="logo-wrap">
          <img src={LOGO} alt="eSIM" />
        </div>
        <h2>Agent Console</h2>
        <div className="sub">Sign in to manage conversations & config</div>

        <div className="login-error">{error}</div>

        <div className="login-field">
          <label>Admin password</label>
          <input
            type="password"
            autoFocus
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        <button className="btn primary" type="submit" disabled={busy} style={{ width: "100%", justifyContent: "center", height: 46 }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
