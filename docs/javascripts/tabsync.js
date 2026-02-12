/**
 * URL-based tab selection for MkDocs Material content tabs.
 *
 * Supports ?tab=<slug> query parameter to pre-select a tab on page load.
 * Tab slugs are lowercase, hyphenated versions of tab labels:
 *   - "Robusta Helm Chart" -> robusta-helm-chart
 *   - "Holmes Helm Chart"  -> holmes-helm-chart
 *   - "Holmes CLI"         -> holmes-cli
 *
 * Usage from external links:
 *   https://holmesgpt.dev/ai-providers/anthropic/?tab=robusta-helm-chart
 *   https://holmesgpt.dev/ai-providers/anthropic/?tab=holmes-cli
 *
 * Uses MkDocs Material's document$ observable so it works with
 * navigation.instant (XHR-based page loads), not just initial load.
 */
document$.subscribe(function () {
  var params = new URLSearchParams(window.location.search);
  var tab = params.get("tab");
  if (!tab) return;

  var targetSlug = tab.toLowerCase();

  // Find all tab labels and click the ones matching the requested slug
  var labels = document.querySelectorAll(".tabbed-labels > label");
  labels.forEach(function (label) {
    var labelSlug = label.textContent
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-+|-+$)/g, "");
    if (labelSlug === targetSlug) {
      label.click();
    }
  });
});
