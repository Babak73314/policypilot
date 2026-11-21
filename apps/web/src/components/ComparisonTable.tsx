import { useTranslation } from "react-i18next";
import type { ComparisonArtifact } from "../lib/api-types";

/** Side-by-side product attributes, straight from the catalog (no model-written values). */
export function ComparisonTable({ comparison }: { comparison: ComparisonArtifact }) {
  const { t } = useTranslation();
  return (
    <div className="card w-full overflow-x-auto">
      <table className="data-table">
        <caption className="sr-only">{t("comparison.caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("comparison.attribute")}</th>
            {comparison.columns.map((column) => (
              <th key={column.productId} scope="col" className="normal-case">
                <span className="block text-sm font-semibold tracking-normal text-slate-900">{column.productName}</span>
                <span className="block font-normal tracking-normal">{column.carrierName}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {comparison.rows.map((row) => (
            <tr key={row.attribute}>
              <th scope="row" className="font-medium tracking-normal text-slate-600 normal-case">
                {row.label}
              </th>
              {row.values.map((value, index) => (
                <td key={comparison.columns[index]?.productId ?? index}>{value ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
