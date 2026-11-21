import { useTranslation } from "react-i18next";
import { Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router";
import { cx } from "../lib/cx";
import { AuthProvider, useAuth } from "./auth";
import { CatalogPage } from "./CatalogPage";
import { ConversationDetailPage } from "./ConversationDetailPage";
import { ConversationsPage } from "./ConversationsPage";
import { LeadsPage } from "./LeadsPage";
import { LoginPage } from "./LoginPage";
import { OutboxPage } from "./OutboxPage";
import { OverviewPage } from "./OverviewPage";
import { ReviewDetailPage } from "./ReviewDetailPage";
import { ReviewsPage } from "./ReviewsPage";

/** The agent dashboard, loaded as its own chunk under /agent (ADR-0016). */
export function AgentApp() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="login" element={<LoginPage />} />
        <Route element={<RequireAgent />}>
          <Route index element={<OverviewPage />} />
          <Route path="reviews" element={<ReviewsPage />} />
          <Route path="reviews/:taskId" element={<ReviewDetailPage />} />
          <Route path="conversations" element={<ConversationsPage />} />
          <Route path="conversations/:conversationId" element={<ConversationDetailPage />} />
          <Route path="leads" element={<LeadsPage />} />
          <Route path="leads/:leadId" element={<Navigate to="/agent/leads" replace />} />
          <Route path="catalog" element={<CatalogPage />} />
          <Route path="outbox" element={<OutboxPage />} />
          <Route path="*" element={<Navigate to="/agent" replace />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}

const NAV = [
  { to: "/agent", key: "overview", end: true },
  { to: "/agent/reviews", key: "reviews", end: false },
  { to: "/agent/conversations", key: "conversations", end: false },
  { to: "/agent/leads", key: "leads", end: false },
  { to: "/agent/catalog", key: "catalog", end: false },
  { to: "/agent/outbox", key: "outbox", end: false },
] as const;

function RequireAgent() {
  const { t } = useTranslation();
  const { agent, logout } = useAuth();
  const location = useLocation();
  if (!agent) return <Navigate to="/agent/login" replace state={{ from: location.pathname }} />;

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col bg-brand-900 text-brand-50">
        <div className="flex items-center gap-2 px-4 py-4">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="font-semibold">{t("agent.title")}</span>
        </div>
        <nav className="flex flex-col gap-0.5 px-2" aria-label={t("agent.navLabel")}>
          {NAV.map((item) => (
            <NavLink
              key={item.key}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cx(
                  "rounded-md px-3 py-2 text-sm",
                  isActive ? "bg-white/15 font-medium text-white" : "hover:bg-white/10",
                )
              }
            >
              {t(`agent.nav.${item.key}`)}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto border-t border-white/10 p-4 text-sm">
          <p className="font-medium">{agent.displayName}</p>
          <p className="truncate text-xs text-brand-100">{agent.email}</p>
          <button type="button" className="mt-3 text-xs underline" onClick={logout}>
            {t("agent.logout")}
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
