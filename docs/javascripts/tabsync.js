/**
 * URL-based tab selection and persistence for MkDocs Material content tabs.
 *
 * How it works:
 * 1. A ?tab=<slug> query parameter sets the preferred tab and saves it
 *    to localStorage so it persists across page navigations.
 * 2. On every page load (including instant navigations), the saved
 *    preference is applied to all tab groups on the page.
 * 3. Clicking a tab manually also updates the saved preference.
 *
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
var STORAGE_KEY = "holmesgpt-tab-pref";

function slugify(text) {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-+|-+$)/g, "");
}

function selectTab(targetSlug) {
  var labels = document.querySelectorAll(".tabbed-labels > label");
  labels.forEach(function (label) {
    if (slugify(label.textContent) === targetSlug) {
      label.click();
    }
  });
}

document$.subscribe(function () {
  // 1. Check URL param — takes priority and updates stored preference
  var params = new URLSearchParams(window.location.search);
  var tabParam = params.get("tab");
  if (tabParam) {
    var slug = tabParam.toLowerCase();
    try { localStorage.setItem(STORAGE_KEY, slug); } catch (e) {}
    selectTab(slug);
  } else {
    // 2. Otherwise apply stored preference
    try {
      var saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        selectTab(saved);
      }
    } catch (e) {}
  }

  // 3. When user clicks a tab, save their preference
  document.querySelectorAll(".tabbed-labels > label").forEach(function (label) {
    label.addEventListener("click", function () {
      try { localStorage.setItem(STORAGE_KEY, slugify(label.textContent)); } catch (e) {}
    });
  });
});
