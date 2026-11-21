import { useTranslation } from "react-i18next";
import { Link, isRouteErrorResponse, useRouteError } from "react-router";

function Shell({ title, body }: { title: string; body: string }) {
  const { t } = useTranslation();
  return (
    <main className="mx-auto flex min-h-screen max-w-lg flex-col items-center justify-center gap-4 p-6 text-center">
      <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
      <p className="text-slate-600">{body}</p>
      <Link className="btn-primary" to="/">
        {t("errors.backHome")}
      </Link>
    </main>
  );
}

export function NotFoundPage() {
  const { t } = useTranslation();
  return <Shell title={t("errors.notFoundTitle")} body={t("errors.notFoundBody")} />;
}

export function ErrorPage() {
  const { t } = useTranslation();
  const error = useRouteError();
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFoundPage />;
  console.error(error);
  return <Shell title={t("errors.crashTitle")} body={t("errors.crashBody")} />;
}
