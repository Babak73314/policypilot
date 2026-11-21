import { useTranslation } from "react-i18next";
import type { QuoteArtifact } from "../lib/api-types";
import { useFormatters } from "../lib/useFormatters";

/**
 * A quote built by the deterministic rating engine. Every amount on the card comes from the
 * stored quote row; the model never produces prices (ADR-0005).
 */
export function QuoteCard({ quote }: { quote: QuoteArtifact }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const breakdown = quote.breakdown.filter((line) => line.amount !== null);

  return (
    <article className="card w-full max-w-sm p-4" aria-label={t("quote.ariaLabel", { product: quote.productName })}>
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-slate-900">{quote.productName}</h3>
          <p className="text-sm text-slate-500">{quote.carrierName}</p>
        </div>
        {quote.overBudget && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
            {t("quote.overBudget")}
          </span>
        )}
      </header>

      <p className="mt-3 text-2xl font-semibold text-brand-900" data-testid="quote-price">
        {quote.rendered}
      </p>
      {quote.monthlyEquivalent && quote.billingPeriod !== "monthly" && (
        <p className="text-sm text-slate-600">
          {t("quote.monthlyEquivalent", { amount: fmt.money(quote.monthlyEquivalent) })}
        </p>
      )}

      {quote.variants.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {quote.variants.map((variant) => (
            <li key={variant.key} className="flex justify-between gap-2 text-slate-700">
              <span>{variant.label}</span>
              <span className="tabular-nums">{variant.premium ? fmt.money(variant.premium) : "—"}</span>
            </li>
          ))}
        </ul>
      )}

      {breakdown.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-brand-700">{t("quote.howCalculated")}</summary>
          <ul className="mt-2 space-y-1">
            {breakdown.map((line) => (
              <li key={line.label} className="flex justify-between gap-2 text-slate-600">
                <span>{line.label}</span>
                <span className="tabular-nums">{line.amount ? fmt.money(line.amount) : ""}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="mt-3 text-xs text-slate-500">{quote.disclaimer}</p>
      <p className="mt-1 text-xs text-slate-400">{t("quote.validUntil", { date: fmt.date(quote.expiresAt) })}</p>
    </article>
  );
}
