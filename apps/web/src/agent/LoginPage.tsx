import { useState, type SubmitEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation, useNavigate } from "react-router";
import { isRecord } from "../lib/guards";
import { isApiError } from "../lib/http";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useAuth } from "./auth";

export function LoginPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("agent.login.pageTitle"));
  const { agent, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const state: unknown = location.state;
  const from = isRecord(state) && typeof state.from === "string" ? state.from : "/agent";
  if (agent) return <Navigate to={from} replace />;

  const onSubmit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      void navigate(from, { replace: true });
    } catch (err) {
      if (isApiError(err, 401)) setError(t("agent.login.invalid"));
      else if (isApiError(err, 429)) setError(t("agent.login.rateLimited"));
      else setError(t("agent.login.failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <form className="card w-full max-w-sm space-y-4 p-6" onSubmit={(event) => void onSubmit(event)}>
        <div>
          <h1 className="text-xl font-semibold text-brand-900">{t("agent.login.heading")}</h1>
          <p className="mt-1 text-sm text-slate-500">{t("agent.login.hint")}</p>
        </div>
        <div>
          <label className="field-label" htmlFor="email">
            {t("agent.login.email")}
          </label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
        </div>
        <div>
          <label className="field-label" htmlFor="password">
            {t("agent.login.password")}
          </label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
            }}
          />
        </div>
        {error && (
          <p className="text-sm text-rose-700" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy ? t("agent.login.signingIn") : t("agent.login.submit")}
        </button>
      </form>
    </main>
  );
}
