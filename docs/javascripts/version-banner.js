(function () {
  var banner = document.getElementById("version-banner");
  if (!banner) return;

  // Determine site root from the canonical URL or location
  // mike serves versions.json at the site root
  var siteRoot = (function () {
    // Try to parse the base URL from the page
    // With mike, URL structure is: <site-root>/<version>/path/to/page/
    // The version selector link gives us a hint, but simplest is to
    // use the known site URL structure
    var loc = window.location;

    // Find where the version segment starts by checking known version paths
    // We'll resolve this after fetching versions.json
    return loc.protocol + "//" + loc.host + "/";
  })();

  fetch(siteRoot + "versions.json")
    .then(function (r) {
      return r.json();
    })
    .then(function (versions) {
      // Determine current version from URL path
      var pathSegments = window.location.pathname.split("/").filter(Boolean);
      if (pathSegments.length === 0) return;

      // Find which path segment matches a known version
      var currentVersion = null;
      for (var s = 0; s < pathSegments.length; s++) {
        for (var v = 0; v < versions.length; v++) {
          var ver = versions[v];
          if (
            ver.version === pathSegments[s] ||
            (ver.aliases && ver.aliases.indexOf(pathSegments[s]) !== -1)
          ) {
            currentVersion = ver;
            break;
          }
        }
        if (currentVersion) break;
      }

      if (!currentVersion) return;

      // Find the stable (latest-aliased) and dev versions
      var latestStable = null;
      var devVersion = null;
      for (var i = 0; i < versions.length; i++) {
        if (
          versions[i].aliases &&
          versions[i].aliases.indexOf("latest") !== -1
        ) {
          latestStable = versions[i];
        }
        if (versions[i].version === "dev") {
          devVersion = versions[i];
        }
      }

      var isDev = currentVersion.version === "dev";
      var isLatest =
        currentVersion.aliases &&
        currentVersion.aliases.indexOf("latest") !== -1;

      var text = "";
      var bannerType = "";

      if (isDev) {
        bannerType = "dev";
        var stableHref = latestStable
          ? siteRoot + latestStable.version + "/"
          : "#";
        text =
          "You are viewing <strong>unreleased</strong> documentation for the next version. " +
          '<a href="' +
          stableHref +
          '"><strong>Switch to the latest stable release.</strong></a>';
      } else if (isLatest) {
        bannerType = "stable";
        var devHref = devVersion ? siteRoot + "dev/" : "#";
        text =
          "You are viewing the <strong>latest stable</strong> release. " +
          '<a href="' +
          devHref +
          '"><strong>View docs for the next unreleased version.</strong></a>';
      } else {
        bannerType = "outdated";
        var stableHref = latestStable
          ? siteRoot + latestStable.version + "/"
          : "#";
        var devHref = devVersion ? siteRoot + "dev/" : "#";
        text =
          "You are viewing docs for an <strong>older version</strong> (" +
          currentVersion.version +
          "). " +
          '<a href="' +
          stableHref +
          '"><strong>Switch to the latest stable release</strong></a>' +
          ' or <a href="' +
          devHref +
          '"><strong>view the unreleased version.</strong></a>';
      }

      banner.setAttribute("data-version-type", bannerType);

      var textEl = document.getElementById("version-banner-text");
      if (textEl) {
        textEl.innerHTML = text;
        banner.removeAttribute("hidden");
      }
    })
    .catch(function () {
      // versions.json not available (e.g., local mkdocs serve) — don't show banner
    });
})();
