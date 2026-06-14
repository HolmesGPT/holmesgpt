/**
 * Deployment picker — turns a `.deployment-picker` tab group into a gated
 * "Which Holmes are you running?" selector.
 *
 * The page author wraps the standard 3-way deployment tabs (Holmes Helm Chart /
 * Robusta Helm Chart / Holmes CLI) in:
 *
 *     <div class="deployment-picker" markdown="1">
 *     === "Holmes Helm Chart"
 *         ...
 *     === "Robusta Helm Chart"
 *         ...
 *     === "Holmes CLI"
 *         ...
 *     </div>
 *
 * This script then:
 *  - Replaces the outer tab strip with a prominent segmented selector.
 *  - Hard-gates the content: no deployment-specific instructions are shown
 *    until the reader picks an option (or a previous choice is restored).
 *  - Persists the choice under its own `holmesgpt-deployment` key, mirrors it
 *    to the `?tab=` URL param, and also writes the shared tabsync.js key
 *    (`holmesgpt-tab-pref`) so deployment tabs elsewhere on the site pick it
 *    up. The dedicated key is authoritative: tabsync.js overwrites the shared
 *    key on every tab click (including the inner method tabs inside each
 *    deployment block), so relying on it alone would lose the choice as soon
 *    as the reader clicked an inner tab.
 *
 * Progressive enhancement: with JavaScript disabled the content stays fully
 * visible (the gating styles only apply once `.is-enhanced` is added), so the
 * instructions remain readable and search-indexable.
 *
 * Inner tab groups inside each deployment block (e.g. "From a GitHub
 * Repository" / "Inline in Helm Values") are left untouched — they are normal
 * MkDocs Material tabs.
 */
// Authoritative key for the picker; survives inner-tab clicks.
const DEPLOY_KEY = "holmesgpt-deployment";
// Shared with tabsync.js so deployment tabs on other pages stay in sync.
const SHARED_KEY = "holmesgpt-tab-pref";

function slugifyDeployment(text) {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-+|-+$)/g, "");
}

function readStored(key) {
  try {
    return localStorage.getItem(key);
  } catch (e) {
    return null;
  }
}

function readPreferredDeployment(isKnownSlug) {
  // Priority: explicit URL param, then the dedicated key, then the shared
  // tabsync key (only if it still holds a valid deployment slug).
  var params = new URLSearchParams(window.location.search);
  var fromUrl = params.get("tab");
  if (fromUrl) {
    var slugged = slugifyDeployment(fromUrl);
    if (isKnownSlug(slugged)) {
      return slugged;
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

function upgradeDeploymentPicker(picker) {
  // The outer tab group is the direct child .tabbed-set; inner method tabs are
  // nested deeper and must not be touched.
  var outerSet = picker.querySelector(":scope > .tabbed-set");
  if (!outerSet) {
    return;
  }
  var radios = Array.from(
    outerSet.querySelectorAll(":scope > input[type='radio']")
  );
  var labels = Array.from(
    outerSet.querySelectorAll(":scope > .tabbed-labels > label")
  );
  if (!labels.length) {
    return;
  }

  var options = labels.map(function (label, index) {
    return {
      slug: slugifyDeployment(label.textContent),
      text: label.textContent.trim(),
      radio: document.getElementById(label.getAttribute("for")) || radios[index],
    };
  });

  // Build the selector UI.
  var selector = document.createElement("div");
  selector.className = "deployment-selector";

  var question = document.createElement("p");
  question.className = "deployment-selector__question";
  question.textContent = "Which Holmes are you running?";
  selector.appendChild(question);

  var optionRow = document.createElement("div");
  optionRow.className = "deployment-selector__options";
  optionRow.setAttribute("role", "group");
  optionRow.setAttribute("aria-label", "Which Holmes are you running?");
  selector.appendChild(optionRow);

  var prompt = document.createElement("p");
  prompt.className = "deployment-selector__prompt";
  prompt.textContent = "Choose your setup above to see the instructions.";
  selector.appendChild(prompt);

  function select(slug, persist) {
    var matched = null;
    options.forEach(function (option) {
      var isActive = option.slug === slug;
      option.button.classList.toggle("is-active", isActive);
      option.button.setAttribute("aria-pressed", isActive ? "true" : "false");
      if (isActive) {
        option.radio.checked = true;
        matched = option;
      }
    });

    if (!matched) {
      radios.forEach(function (radio) {
        radio.checked = false;
      });
      picker.classList.add("no-selection");
      return;
    }

    picker.classList.remove("no-selection");
    matched.radio.dispatchEvent(new Event("change", { bubbles: true }));

    if (persist) {
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

  options.forEach(function (option) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "deployment-selector__option";
    button.textContent = option.text;
    button.setAttribute("data-deployment", option.slug);
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", function () {
      select(option.slug, true);
    });
    option.button = button;
    optionRow.appendChild(button);
  });

  picker.insertBefore(selector, outerSet);
  picker.classList.add("is-enhanced");

  // Start gated, then restore any saved/linked choice.
  radios.forEach(function (radio) {
    radio.checked = false;
  });
  picker.classList.add("no-selection");

  function isKnownSlug(slug) {
    return options.some(function (option) {
      return option.slug === slug;
    });
  }

  var preferred = readPreferredDeployment(isKnownSlug);
  if (preferred) {
    select(preferred, false);
  }
}

document$.subscribe(function () {
  document
    .querySelectorAll(".deployment-picker:not(.is-enhanced)")
    .forEach(upgradeDeploymentPicker);
});
