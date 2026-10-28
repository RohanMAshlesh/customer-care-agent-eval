import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import JourneyPage from "./pages/Journey";
import LabPage from "./pages/Lab";
import GatePage from "./pages/Gate";
import OnboardingPage from "./pages/Onboarding";
import HowPage from "./pages/How";

const ROUTES = [
  { id: "journey", label: "Refund journey", page: JourneyPage },
  { id: "lab", label: "Edge-case lab", page: LabPage },
  { id: "release", label: "Release gate", page: GatePage },
  { id: "onboarding", label: "Client onboarding", page: OnboardingPage },
  { id: "how", label: "How it works", page: HowPage },
];

function useRoute() {
  const read = () => ROUTES.find((r) => `#${r.id}` === window.location.hash)?.id ?? "journey";
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
    try { theme ? localStorage.setItem("theme", theme) : localStorage.removeItem("theme"); } catch { /* ignore */ }
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
  const Page = ROUTES.find((r) => r.id === route)!.page;
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="#journey">
            <span className="brand-mark" aria-hidden>
              <svg width="18" height="18" viewBox="0 0 32 32"><path d="M8 17l5 5L24 10" stroke="#73e0c4" strokeWidth="3.4" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </span>
            <span>
              <span className="brand-name">Evidence Desk</span>
              <span className="brand-sub">Customer-care agent evaluation</span>
            </span>
          </a>
          <nav className="nav" aria-label="Sections">
            {ROUTES.map((r) => (
              <a key={r.id} href={`#${r.id}`} aria-current={route === r.id ? "page" : undefined}>{r.label}</a>
            ))}
          </nav>
          <ThemeToggle />
        </div>
      </header>
      <main className="page">
        <Page />
        <footer className="footer">
          <span>Every verdict on this page was computed by the open-source evaluation engine, then exported as static data.</span>
          <span>Amounts, policies and thresholds are illustrative examples, not any marketplace's real rules.</span>
        </footer>
      </main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
