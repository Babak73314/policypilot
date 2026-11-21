import { useEffect } from "react";
import { useTranslation } from "react-i18next";

/** Sets `document.title` to "<page> · PolicyPilot" while the calling page is mounted. */
export function useDocumentTitle(page: string): void {
  const { t } = useTranslation();
  const title = t("documentTitle", { page });

  useEffect(() => {
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
