import { NavLink, Outlet } from "react-router-dom";

const navItems = [
  { to: "/", label: "Library" },
  { to: "/drive", label: "Drive" },
  { to: "/podcasts", label: "Podcasts" }
] as const;

export function AuroraShell(): React.ReactElement {
  return (
    <div className="min-h-screen bg-aurora-night text-aurora-text">
      <header className="border-b border-aurora-border">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
          <div>
            <p className="text-sm uppercase tracking-[0.18em] text-aurora-cyan">Aurora</p>
            <h1 className="text-2xl font-semibold tracking-normal">Player</h1>
          </div>
          <nav aria-label="Primary navigation" className="flex gap-2">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                className={({ isActive }) =>
                  [
                    "rounded-md px-4 py-2 text-sm font-medium outline-none transition",
                    "focus-visible:ring-2 focus-visible:ring-aurora-cyan",
                    isActive
                      ? "bg-aurora-cyan text-aurora-night"
                      : "text-aurora-muted hover:bg-aurora-panel hover:text-aurora-text"
                  ].join(" ")
                }
                to={item.to}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
