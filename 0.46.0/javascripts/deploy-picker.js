/**
 * Deployment picker — site-wide "Which Holmes are you running?" selector.
 *
 * Many docs pages present the same configuration three ways: Holmes CLI, the
 * standalone Holmes Helm Chart, and the Robusta Helm Chart (HolmesGPT
 * Enterprise). Out of the box MkDocs renders these as tab strips, which (a)
 * don't make it obvious the reader is meant to pick their own platform and
 * (b) stack confusingly when nested inside other tabs.
 *
 * This script finds every tab group whose tabs are exactly the deployment
 * options — on any page, no per-page markup required — and upgrades it:
 *  - The native tab strip is replaced with a labelled segmented selector, so
 *    the Robusta option can be spelled out as "HolmesGPT Enterprise".
 *  - Until the reader picks, the configuration is shown behind a
 *    semi-transparent overlay carrying the selector: the content stays visible
 *    but is clearly "locked" so the choice can't be missed.
 *  - The choice is global: picking once reveals every deployment block on the
 *    page, is remembered across pages, and is mirrored to the ?tab= URL param
 *    and the tabsync.js key so plain deployment tabs elsewhere stay in sync.
 *
 * Progressive enhancement: the gating styles only apply once the script adds
 * `.is-enhanced`, so with JavaScript disabled every variant stays visible and
 * search-indexable.
 */

// Authoritative key for the picker; survives inner-tab clicks. tabsync.js
// overwrites the shared key on every tab click (including the inner method
// tabs inside a deployment block), so relying on it alone would lose the
// choice as soon as the reader clicked an inner tab.
const DEPLOY_KEY = "holmesgpt-deployment";
// Shared with tabsync.js so plain deployment tabs on other pages stay in sync.
const SHARED_KEY = "holmesgpt-tab-pref";

// The deployment options, keyed by slug. The value is the label shown in the
// selector; the underlying tab label stays "Robusta Helm Chart" everywhere, so
// tab slugs and cross-page/-tab sync are unaffected by the friendlier wording.
const DEPLOYMENTS = {
  "holmes-cli": "Holmes OSS — CLI",
  "holmes-helm-chart": "Holmes OSS — Helm Chart",
  "robusta-helm-chart": "HolmesGPT Enterprise — Robusta Helm Chart",
};

function slugify(text) {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-+|-+$)/g, "");
}

function isKnownSlug(slug) {
  return Object.prototype.hasOwnProperty.call(DEPLOYMENTS, slug);
}

function readStored(key) {
  try {
    return localStorage.getItem(key);
  } catch (e) {
    return null;
  }
}

function readPreferredDeployment() {
  // Priority: explicit URL param, then the dedicated key, then the shared
  // tabsync key (only if it still holds a valid deployment slug).
  var params = new URLSearchParams(window.location.search);
  var fromUrl = params.get("tab");
  if (fromUrl) {
    var fromUrlSlug = slugify(fromUrl);
    if (isKnownSlug(fromUrlSlug)) {
      return fromUrlSlug;
    }
  }
  var dedicated = readStored(DEPLOY_KEY);
  if (dedicated && isKnownSlug(dedicated)) {
    return dedicated;
  }
  var shared = readStored(SHARED_KEY);
  if (shared && isKnownSlug(shared)) {
    return shared;
  }
  return null;
}

// A tab group is a deployment group when every one of its (top-level) tab
// labels is a known deployment option. Inner tab groups (e.g. "From a GitHub
// Repository") don't match and are left as normal tabs.
function deploymentOptionsFor(set) {
  var labels = Array.from(
    set.querySelectorAll(":scope > .tabbed-labels > label")
  );
  if (labels.length < 2) {
    return null;
  }
  var radios = Array.from(
    set.querySelectorAll(":scope > input[type='radio']")
  );
  var options = [];
  for (var i = 0; i < labels.length; i++) {
    var slug = slugify(labels[i].textContent);
    if (!isKnownSlug(slug)) {
      return null;
    }
    options.push({
      slug: slug,
      radio: document.getElementById(labels[i].getAttribute("for")) || radios[i],
    });
  }
  return options;
}

// Every upgraded set on the current page, so one click can update them all.
var registry = [];

function applyGlobalChoice(slug, persist) {
  registry.forEach(function (entry) {
    entry.apply(slug);
  });
  if (persist && slug) {
    try {
      localStorage.setItem(DEPLOY_KEY, slug);
      localStorage.setItem(SHARED_KEY, slug);
    } catch (e) {
      /* ignore storage errors (private mode, etc.) */
    }
    var url = new URL(window.location);
    url.searchParams.set("tab", slug);
    history.replaceState(null, "", url);
  }
}

function upgradeSet(set, options) {
  // The selector is a dropdown (not a row of pills): it stays compact and never
  // wraps, even with a long label like "HolmesGPT Enterprise — Robusta Helm
  // Chart" in a narrow content column. It doubles as the in-overlay picker
  // (while gated) and the slim switcher (after a choice has been made).
  var selector = document.createElement("div");
  selector.className = "deployment-selector";

  var question = document.createElement("p");
  question.className = "deployment-selector__question";
  question.textContent = "Which Holmes are you running?";
  selector.appendChild(question);

  var control = document.createElement("div");
  control.className = "deployment-selector__control";

  var inlineLabel = document.createElement("span");
  inlineLabel.className = "deployment-selector__label";
  inlineLabel.textContent = "Instructions for";
  control.appendChild(inlineLabel);

  var select = document.createElement("select");
  select.className = "deployment-selector__select";
  select.setAttribute("aria-label", "Which Holmes are you running?");

  var placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Select your setup…";
  placeholder.disabled = true;
  placeholder.selected = true;
  select.appendChild(placeholder);

  options.forEach(function (option) {
    var opt = document.createElement("option");
    opt.value = option.slug;
    opt.textContent = DEPLOYMENTS[option.slug];
    select.appendChild(opt);
  });
  select.addEventListener("change", function () {
    if (select.value) {
      applyGlobalChoice(select.value, true);
    }
  });
  control.appendChild(select);
  selector.appendChild(control);

  var hint = document.createElement("p");
  hint.className = "deployment-selector__hint";
  hint.textContent = "Pick your setup to view the configuration.";
  selector.appendChild(hint);

  var content = set.querySelector(":scope > .tabbed-content");
  set.insertBefore(selector, content);
  // Start enhanced + gated; the first tab stays checked so there is real
  // (blurred) content behind the overlay.
  set.classList.add("is-enhanced", "is-gated");

  function apply(slug) {
    var match = null;
    options.forEach(function (option) {
      if (option.slug === slug) {
        match = option;
      }
    });
    // When the global choice isn't one of this group's options (e.g. a
    // Helm-only block when the reader picked CLI) we keep the first tab rather
    // than forcing a second choice.
    var shown = match || options[0];
    shown.radio.checked = true;
    shown.radio.dispatchEvent(new Event("change", { bubbles: true }));
    select.value = shown.slug;
    set.classList.remove("is-gated");
  }

  registry.push({ set: set, apply: apply });
}

document$.subscribe(function () {
  registry = [];
  document
    .querySelectorAll(".tabbed-set:not(.is-enhanced)")
    .forEach(function (set) {
      var options = deploymentOptionsFor(set);
      if (options) {
        upgradeSet(set, options);
      }
    });
  var preferred = readPreferredDeployment();
  if (preferred) {
    // If the choice arrived via the ?tab= URL param (e.g. an external link
    // from Robusta Enterprise), persist it so it becomes authoritative — it
    // then survives an inner-tab click clobbering the shared tabsync key and
    // carries to other pages, just like an explicit pick. Restores from
    // storage don't need to be re-persisted.
    var fromUrl = new URLSearchParams(window.location.search).get("tab");
    var cameFromUrl = !!(fromUrl && slugify(fromUrl) === preferred);
    applyGlobalChoice(preferred, cameFromUrl);
  }
});
