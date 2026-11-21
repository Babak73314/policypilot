import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { CatalogProduct, Page, RateTable } from "../lib/api-types";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useFormatters } from "../lib/useFormatters";
import { useAuth } from "./auth";
import { KeyValues, PageHeader, QueryState, Section } from "./ui";

/** The fictional catalog and its versioned rate tables, read-only (ADR-0005). */
export function CatalogPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("agent.nav.catalog"));
  const { call } = useAuth();
  const [selected, setSelected] = useState<CatalogProduct | null>(null);
  const products = useQuery({
    queryKey: ["catalog"],
    queryFn: () => call<Page<CatalogProduct>>("/api/v1/agent/catalog/products"),
    staleTime: 5 * 60_000,
  });

  return (
    <>
      <PageHeader title={t("agent.catalog.heading")} />
      <QueryState isPending={products.isPending} error={products.error} />
      {products.data && (
        <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
          <div className="card overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t("agent.catalog.product")}</th>
                  <th>{t("agent.catalog.line")}</th>
                  <th>{t("agent.catalog.carrier")}</th>
                </tr>
              </thead>
              <tbody>
                {products.data.items.map((product) => (
                  <tr key={product.id} className={selected?.id === product.id ? "bg-brand-50" : undefined}>
                    <td>
                      <button
                        type="button"
                        className="text-left font-medium text-brand-700 hover:underline"
                        onClick={() => {
                          setSelected(product);
                        }}
                      >
                        {product.name}
                      </button>
                      <span className="block font-mono text-xs text-slate-400">{product.code}</span>
                    </td>
                    <td>{t(`lines.${product.line}`)}</td>
                    <td>{product.carrier.name}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selected ? (
            <ProductDetail product={selected} />
          ) : (
            <p className="text-sm text-slate-500">{t("agent.catalog.pick")}</p>
          )}
        </div>
      )}
    </>
  );
}

function ProductDetail({ product }: { product: CatalogProduct }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const { call } = useAuth();
  const tables = useQuery({
    queryKey: ["rate-tables", product.id],
    queryFn: () => call<Page<RateTable>>(`/api/v1/agent/catalog/products/${product.id}/rate-tables`),
    staleTime: 5 * 60_000,
  });

  return (
    <div className="space-y-4">
      <Section title={product.name}>
        <p className="mb-3 text-sm text-slate-700">{product.summary}</p>
        <KeyValues data={product.attributes} />
      </Section>
      <Section title={t("agent.catalog.rateTables")}>
        <QueryState isPending={tables.isPending} error={tables.error} />
        {tables.data?.items.map((table) => (
          <details key={table.id} className="mb-2 text-sm" open={tables.data.items.length === 1}>
            <summary className="cursor-pointer">
              {t("agent.catalog.version", {
                version: table.version,
                from: fmt.date(table.effectiveFrom),
                to: table.effectiveTo ? fmt.date(table.effectiveTo) : t("agent.catalog.openEnded"),
              })}
            </summary>
            <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
              {JSON.stringify(table.factors, null, 2)}
            </pre>
          </details>
        ))}
      </Section>
    </div>
  );
}
