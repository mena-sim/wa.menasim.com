import { useCallback, useEffect, useRef, useState } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "./api.js";
import Login from "./components/Login.jsx";
import Rail from "./components/Rail.jsx";
import Conversations from "./components/Conversations.jsx";
import Settings from "./components/Settings.jsx";

export default function App() {
  const [authed, setAuthed] = useState(false);
  const [checking, setChecking] = useState(true);
  const [view, setView] = useState("chats");

  // Toast
  const [toast, setToast] = useState({ msg: "", err: false, show: false });
  const toastTimer = useRef(null);
  const showToast = useCallback((msg, err = false) => {
    setToast({ msg, err, show: true });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast((t) => ({ ...t, show: false })), 2600);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthed(false));
    (async () => {
      if (getToken()) {
        try {
          await api.me();
          setAuthed(true);
        } catch {
          setAuthed(false);
        }
      }
      setChecking(false);
    })();
  }, []);

  const toggleTheme = () => {
    document.body.classList.toggle("dark");
    localStorage.setItem("menasim_theme", document.body.classList.contains("dark") ? "dark" : "light");
  };

  useEffect(() => {
    if (localStorage.getItem("menasim_theme") === "dark") document.body.classList.add("dark");
  }, []);

  const logout = async () => {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    setToken("");
    setAuthed(false);
  };

  if (checking) return null;
  if (!authed) return <Login onLogin={() => setAuthed(true)} />;

  return (
    <>
      <Rail view={view} setView={setView} onToggleTheme={toggleTheme} onLogout={logout} />
      {view === "chats" ? (
        <Conversations toast={showToast} />
      ) : (
        <Settings toast={showToast} />
      )}
      <div className={`toast ${toast.show ? "show" : ""} ${toast.err ? "err" : ""}`}>{toast.msg}</div>
    </>
  );
}
