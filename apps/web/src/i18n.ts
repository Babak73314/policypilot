import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en/translation.json";

/**
 * UI text lives in resource files (ADR-0013). English is the only language in Phase 1; a second,
 * left-to-right language is added by dropping `src/locales/<lang>/translation.json` next to this
 * one and listing it here. Formatting follows the active language (`lib/useFormatters`).
 */
export const SUPPORTED_LANGUAGES = ["en"] as const;

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: "en",
  fallbackLng: "en",
  supportedLngs: SUPPORTED_LANGUAGES,
  interpolation: { escapeValue: false }, // React escapes output
  returnNull: false,
});

export default i18n;
