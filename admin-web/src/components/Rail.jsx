import Icon from "./Icon.jsx";
import { LOGO } from "../icons.js";

export default function Rail({ view, setView, onToggleTheme, onLogout }) {
  return (
    <div className="rail">
      <div className="logo-wrap">
        <img src={LOGO} alt="eSIM" />
      </div>
      <button className={view === "chats" ? "active" : ""} title="WhatsApp" onClick={() => setView("chats")}>
        <Icon name="chat" />
      </button>
      <button className={view === "settings" ? "active" : ""} title="Settings" onClick={() => setView("settings")}>
        <Icon name="settings" />
      </button>
      <div className="spacer" />
      <div className="theme-toggle" title="Toggle theme" onClick={onToggleTheme}>
        <Icon name="theme" />
      </div>
      <button title="Logout" onClick={onLogout}>
        <Icon name="logout" />
      </button>
      <div className="status-pill">
        <div className="status-dot" />
      </div>
    </div>
  );
}
