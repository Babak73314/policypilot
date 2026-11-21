import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useDocumentTitle } from "../lib/useDocumentTitle";

const SECTIONS = ["who", "what", "models", "retention", "rights", "demo"] as const;

/** The privacy notice linked from the AI disclosure (docs/compliance.md). */
export function PrivacyPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("privacy.pageTitle"));
  return (
    <main className="mx-auto max-w-2xl px-4 py-10">
      <Link to="/" className="text-sm text-brand-700 hover:underline">
        {t("privacy.back")}
      </Link>
      <h1 className="mt-4 text-3xl font-semibold text-brand-900">{t("privacy.heading")}</h1>
      <p className="mt-3 text-slate-600">{t("privacy.intro")}</p>
      {SECTIONS.map((section) => (
        <section key={section} className="mt-8">
          <h2 className="text-lg font-semibold text-slate-900">{t(`privacy.sections.${section}.title`)}</h2>
          <p className="mt-2 leading-relaxed text-slate-700">{t(`privacy.sections.${section}.body`)}</p>
        </section>
      ))}
    </main>
  );
}
