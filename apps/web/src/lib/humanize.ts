/**
 * Turns an API field name into a readable label: "lineInterest" and "line_interest" both become
 * "Line interest". Used for free-form records (snapshot data, profiles, run summaries) whose
 * keys come from the server rather than from the UI's resource files.
 */
export function humanizeKey(key: string): string {
  const words = key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_\-.]+/g, " ")
    .trim()
    .toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
