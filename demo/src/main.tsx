import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import OverviewPage from "./pages/Overview";
import JourneyPage from "./pages/Journey";
import CatchesPage from "./pages/Catches";
import ShipPage from "./pages/Ship";
import SetupPage from "./pages/Setup";

const ROUTES = [
  { id: "overview", label: "Overview", page: OverviewPage },
  { id: "journey", label: "1 · Four cases", page: JourneyPage },
  { id: "catches", label: "2 · What it catches", page: CatchesPage },
  { id: "ship", label: "3 · Ship or not", page: ShipPage },
  { id: "setup", label: "4 · Getting started", page: SetupPage },
];

function useRoute() {
  const read = () => {
    const h = window.location.hash.replace("#", "");
    if (h.startsWith("journey-")) return h;
    return ROUTES.find((r) => r.id === h)?.id ?? "overview";
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => { setRoute(read()); window.scrollTo({ top: 0 }); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

function ThemeToggle() {
  const [theme, setTheme] = useState<string | null>(() => {
    try { return localStorage.getItem("theme"); } catch { return null; }
  });
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    try { if (theme) localStorage.setItem("theme", theme); else localStorage.removeItem("theme"); } catch { /* ignore */ }
  }, [theme]);
  const dark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  return (
    <button className="theme-toggle" aria-label={`Switch to ${dark ? "light" : "dark"} theme`} onClick={() => setTheme(dark ? "light" : "dark")}>
      {dark ? "☀" : "☾"}
    </button>
  );
}

function App() {
  const route = useRoute();
  const base = route.startsWith("journey-") ? "journey" : route;
  const Page = ROUTES.find((r) => r.id === base)!.page as (p: { initial?: string }) => JSX.Element;
  const initial = route.startsWith("journey-") ? route.slice("journey-".length) : undefined;
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="#overview">
            <span className="brand-mark" aria-hidden>
              <svg width="18" height="18" viewBox="0 0 32 32"><path d="M8 17l5 5L24 10" stroke="#73e0c4" strokeWidth="3.4" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </span>
            <span>
              <span className="brand-name">Evidence Desk</span>
              <span className="brand-sub">Resolution, not conversation</span>
            </span>
          </a>
          <nav className="nav" aria-label="Sections">
            {ROUTES.map((r) => (
              <a key={r.id} href={`#${r.id}`} aria-current={base === r.id ? "page" : undefined}>{r.label}</a>
            ))}
          </nav>
          <ThemeToggle />
        </div>
      </header>
      <main className="page">
        <Page initial={initial} />
        <footer className="footer">
          <span>Interactive prototype. Every verdict shown was produced by the real evaluation engine and saved as data.</span>
          <span>Customers, tickets, amounts and policies are illustrative, not any company’s real data or rules.</span>
        </footer>
      </main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
