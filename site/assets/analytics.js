(function () {
  "use strict";

  var config = window.ANALYTICS_CONFIG || {};
  var measurementId = String(config.measurementId || "").trim();
  var enabled = /^G-[A-Z0-9]+$/i.test(measurementId);
  var queue = window.dataLayer = window.dataLayer || [];
  var moduleIds = {
    activitySection: "activity",
    playbookSection: "playbook",
    selectionSection: "selection",
    creativeSection: "creative",
    caseSection: "cases",
    topTabs: "selection",
    linkTabs: "selection",
    trackGrid: "creative",
    detail: "creative"
  };

  function cleanParams(params) {
    var result = {};
    Object.keys(params || {}).forEach(function (key) {
      var value = params[key];
      if (value === undefined || value === null || value === "") return;
      result[key] = typeof value === "string" ? value.slice(0, 100) : value;
    });
    return result;
  }

  function gtag() {
    queue.push(arguments);
  }

  function track(eventName, params) {
    var payload = cleanParams(Object.assign({
      page_path: location.pathname,
      page_title: document.title
    }, params || {}));
    if (enabled) gtag("event", eventName, payload);
    window.dispatchEvent(new CustomEvent("intel:analytics", { detail: { event: eventName, params: payload } }));
  }

  function moduleNameFor(element) {
    var current = element;
    while (current) {
      if (current.dataset && current.dataset.analyticsModule) return current.dataset.analyticsModule;
      if (current.id && moduleIds[current.id]) return moduleIds[current.id];
      if (current === document.body) break;
      current = current.parentElement;
    }
    return document.body.dataset.page || "unknown";
  }

  window.IntelAnalytics = {
    enabled: enabled,
    track: track,
    moduleView: function (moduleName, params) {
      track("module_view", Object.assign({ module_name: moduleName }, params || {}));
    },
    moduleClick: function (moduleName, action, params) {
      track("module_click", Object.assign({ module_name: moduleName, action: action }, params || {}));
    }
  };

  if (enabled) {
    var script = document.createElement("script");
    script.async = true;
    script.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(measurementId);
    document.head.appendChild(script);
    gtag("js", new Date());
    gtag("config", measurementId, { send_page_view: false, anonymize_ip: true });
  }

  track("page_view", {
    page_location: location.href,
    page_referrer: document.referrer,
    page_type: document.body.dataset.page || "unknown"
  });

  document.addEventListener("click", function (event) {
    var target = event.target.closest("a,button,.tab,.cf-item,.tl-item,.tofu,.track-mini-card,.node-card,.activity-item,.playbook-entry,[data-analytics-event]");
    if (!target) return;
    var moduleName = moduleNameFor(target);
    var action = target.dataset.analyticsAction || target.dataset.v || target.dataset.l || "open";
    var itemName = target.dataset.analyticsItem || target.dataset.id || target.textContent.trim().replace(/\s+/g, " ").slice(0, 80);
    track(target.dataset.analyticsEvent || "module_click", {
      module_name: moduleName,
      action: action,
      item_name: itemName,
      link_url: target.href || ""
    });
  });

  function observeModules() {
    if (!("IntersectionObserver" in window)) return;
    var seen = {};
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var moduleName = moduleNameFor(entry.target);
        if (seen[moduleName]) return;
        seen[moduleName] = true;
        window.IntelAnalytics.moduleView(moduleName);
      });
    }, { threshold: 0.35 });
    Object.keys(moduleIds).forEach(function (id) {
      var element = document.getElementById(id);
      if (element && !element.hidden) observer.observe(element);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", observeModules);
  else observeModules();
})();
