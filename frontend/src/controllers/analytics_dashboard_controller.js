import { Controller } from "@hotwired/stimulus";

const DAY = 24 * 60 * 60 * 1000;

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

function formatPercent(value) {
  return `${Number(value || 0).toFixed(2)}%`;
}

function shortDateLabel(dateString) {
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) {
    return dateString;
  }
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function toLocalIsoDate(date) {
  const localDate = new Date(date);
  localDate.setMinutes(localDate.getMinutes() - localDate.getTimezoneOffset());
  return localDate.toISOString().slice(0, 10);
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function toRangeFingerprint(startDate, endDate) {
  return `${startDate || ""}:${endDate || ""}`;
}

export default class extends Controller {
  static targets = [
    "preset",
    "startDate",
    "endDate",
    "error",
    "windowLabel",
    "kpiClicks",
    "kpiImpressions",
    "kpiSessions",
    "kpiUsers",
    "kpiConversions",
    "kpiCtr",
    "kpiConversionRate",
    "sourceHealth",
    "trendEmpty",
    "trendBars",
    "breakdownBody",
    "pagesBody",
  ];

  static values = {
    projectId: Number,
  };

  connect() {
    const defaultPreset = this.currentPreset();
    const { start, end } = this.rangeFromPreset(defaultPreset);
    this.startDateTarget.value = start;
    this.endDateTarget.value = end;
    this.setPresetUi(defaultPreset);

    this.lastRangeFingerprint = toRangeFingerprint(start, end);
    this.sourceErrorFingerprintsSeen = new Set();

    this.captureEvent("analytics_page_viewed", {
      ...this.currentRangeTelemetry(),
      selected_preset_days: Number(defaultPreset || 30),
    });

    this.load({ triggerSource: "initial_page_load" });
  }

  async refresh(event) {
    if (event) {
      event.preventDefault();
    }
    this.setPresetUi("custom");

    this.captureDateRangeChangedIfNeeded({
      change_source: "custom_date_refresh",
    });

    this.captureEvent("analytics_refresh_clicked", {
      ...this.currentRangeTelemetry(),
      trigger_source: "refresh_button",
    });

    await this.load({ triggerSource: "refresh_button" });
  }

  async applyPreset(event) {
    event.preventDefault();
    const days = event.currentTarget.dataset.days;
    if (!days) {
      return;
    }

    const { start, end } = this.rangeFromPreset(days);
    this.startDateTarget.value = start;
    this.endDateTarget.value = end;
    this.setPresetUi(days);

    this.captureDateRangeChangedIfNeeded({
      change_source: "preset_click",
      selected_preset_days: Number(days),
    });

    await this.load({ triggerSource: "preset_click" });
  }

  async load({ triggerSource = "unknown" } = {}) {
    this.errorTarget.classList.add("hidden");
    this.sourceErrorFingerprintsSeen = new Set();

    const startDate = this.startDateTarget.value;
    const endDate = this.endDateTarget.value;
    if (!startDate || !endDate) {
      this.renderError("Pick both start and end dates.", {
        captureTelemetry: false,
      });
      return;
    }

    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
    });

    try {
      const response = await fetch(`/api/projects/${this.projectIdValue}/analytics/aggregation?${params.toString()}`);
      const payload = await response.json();

      if (!response.ok || payload.status !== "success") {
        const error = new Error(payload.message || "Failed to load analytics.");
        error.telemetryMeta = {
          source: "dashboard_api",
          source_status: response.status,
          trigger_source: triggerSource,
        };
        throw error;
      }

      this.render(payload);
    } catch (error) {
      this.renderError(error.message || "Failed to load analytics.", {
        source: "dashboard_api",
        trigger_source: triggerSource,
        source_status: error?.telemetryMeta?.source_status || "fetch_failed",
      });
    }
  }

  render(payload) {
    const overview = payload.overview || {};
    const dateRange = payload.date_range || {};

    this.windowLabelTarget.textContent = `${dateRange.start_date || "—"} → ${dateRange.end_date || "—"} (${dateRange.days || 0} days)`;

    this.kpiClicksTarget.textContent = formatNumber(overview.clicks);
    this.kpiImpressionsTarget.textContent = formatNumber(overview.impressions);
    this.kpiSessionsTarget.textContent = formatNumber(overview.sessions);
    this.kpiUsersTarget.textContent = formatNumber(overview.users);
    this.kpiConversionsTarget.textContent = Number(overview.conversions || 0).toFixed(2);
    this.kpiCtrTarget.textContent = formatPercent(overview.ctr_pct);
    this.kpiConversionRateTarget.textContent = formatPercent(overview.conversion_rate_pct);

    this.renderSourceHealth(payload.source_health || []);
    this.renderTrend(payload.daily_trend || []);
    this.renderSourceBreakdown(payload.source_breakdown || []);
    this.renderPageBreakdown(payload.page_breakdown || []);
  }

  renderSourceHealth(rows) {
    if (!rows.length) {
      this.sourceHealthTarget.innerHTML = '<p class="text-sm text-gray-500">No source status available yet.</p>';
      return;
    }

    const html = rows
      .map((row) => {
        const source = (row.source || "unknown").toUpperCase();
        const badge = this.healthBadge(row);
        const detail = this.healthDetail(row);

        if (row.last_error) {
          const fingerprint = `${row.source || "unknown"}:${row.status || "unknown"}:${row.last_error}`;
          if (!this.sourceErrorFingerprintsSeen.has(fingerprint)) {
            this.sourceErrorFingerprintsSeen.add(fingerprint);
            this.captureEvent("analytics_source_error_shown", {
              ...this.currentRangeTelemetry(),
              source: row.source || "unknown",
              source_status: row.status || "unknown",
              stale_days: row.stale_days,
              error_message: row.last_error,
              result_status: "shown",
            });
          }
        }

        return `
          <li class="flex flex-wrap gap-2 justify-between items-center px-3 py-2 rounded-md border border-gray-200 bg-gray-50">
            <div>
              <p class="text-sm font-medium text-gray-900">${escapeHtml(source)}</p>
              <p class="text-xs text-gray-500">${escapeHtml(detail)}</p>
            </div>
            <span class="inline-flex items-center px-2.5 py-1 text-xs font-semibold rounded-full ${badge.classes}">${badge.label}</span>
          </li>
        `;
      })
      .join("");

    this.sourceHealthTarget.innerHTML = `<ul class="space-y-2">${html}</ul>`;
  }

  renderTrend(rows) {
    if (!rows.length || rows.every((row) => Number(row.sessions || 0) === 0 && Number(row.clicks || 0) === 0)) {
      this.trendEmptyTarget.classList.remove("hidden");
      this.trendBarsTarget.classList.add("hidden");
      this.trendBarsTarget.innerHTML = "";
      return;
    }

    this.trendEmptyTarget.classList.add("hidden");
    this.trendBarsTarget.classList.remove("hidden");

    const maxSessions = Math.max(...rows.map((row) => Number(row.sessions || 0)), 1);
    const bars = rows
      .map((row) => {
        const sessions = Number(row.sessions || 0);
        const clicks = Number(row.clicks || 0);
        const height = Math.max(Math.round((sessions / maxSessions) * 100), sessions > 0 ? 8 : 2);
        return `
          <div class="flex flex-col flex-1 gap-1 justify-end min-w-0" title="${escapeHtml(row.date)}: ${sessions} sessions, ${clicks} clicks">
            <div class="w-full bg-pink-300 rounded-sm" style="height:${height}px"></div>
          </div>
        `;
      })
      .join("");

    const labels = [rows[0], rows[rows.length - 1]]
      .filter(Boolean)
      .map((row) => `<span>${escapeHtml(shortDateLabel(row.date))}</span>`)
      .join("");

    this.trendBarsTarget.innerHTML = `
      <div class="h-28 flex items-end gap-1">${bars}</div>
      <div class="flex justify-between mt-2 text-xs text-gray-500">${labels}</div>
    `;
  }

  renderSourceBreakdown(rows) {
    if (!rows.length) {
      this.breakdownBodyTarget.innerHTML = `
        <tr>
          <td colspan="6" class="px-3 py-4 text-sm text-gray-500">No source breakdown data yet.</td>
        </tr>
      `;
      return;
    }

    this.breakdownBodyTarget.innerHTML = rows
      .map((row) => {
        return `
          <tr class="border-t border-gray-100">
            <td class="px-3 py-2 text-sm font-medium text-gray-900 uppercase">${escapeHtml(row.source)}</td>
            <td class="px-3 py-2 text-sm text-gray-700">${formatNumber(row.clicks)}</td>
            <td class="px-3 py-2 text-sm text-gray-700">${formatNumber(row.impressions)}</td>
            <td class="px-3 py-2 text-sm text-gray-700">${formatNumber(row.sessions)}</td>
            <td class="px-3 py-2 text-sm text-gray-700">${formatNumber(row.users)}</td>
            <td class="px-3 py-2 text-sm text-gray-700">${Number(row.conversions || 0).toFixed(2)}</td>
          </tr>
        `;
      })
      .join("");
  }

  renderPageBreakdown(rows) {
    if (!rows.length) {
      this.pagesBodyTarget.innerHTML = `
        <tr>
          <td colspan="4" class="px-3 py-4 text-sm text-gray-500">No page-level search data yet.</td>
        </tr>
      `;
      return;
    }

    this.pagesBodyTarget.innerHTML = rows
      .map((row) => {
        return `
          <tr class="border-t border-gray-100">
            <td class="px-3 py-2 text-sm text-gray-800 break-all">${escapeHtml(row.page_url)}</td>
            <td class="px-3 py-2 text-sm text-gray-700">${formatNumber(row.clicks)}</td>
            <td class="px-3 py-2 text-sm text-gray-700">${formatNumber(row.impressions)}</td>
            <td class="px-3 py-2 text-sm text-gray-700">${formatPercent(row.ctr_pct)}</td>
          </tr>
        `;
      })
      .join("");
  }

  healthBadge(row) {
    if (!row.integration_connected) {
      return {
        label: "Missing",
        classes: "text-gray-700 bg-gray-100",
      };
    }

    if (row.status === "stale" || row.status === "degraded" || row.status === "pending") {
      return {
        label: "Stale",
        classes: "text-amber-800 bg-amber-100",
      };
    }

    return {
      label: "Connected",
      classes: "text-green-800 bg-green-100",
    };
  }

  healthDetail(row) {
    if (!row.integration_connected) {
      return "Integration not connected.";
    }

    if (row.last_error) {
      return `Last sync issue: ${row.last_error}`;
    }

    if (row.stale_days === null || row.stale_days === undefined) {
      return row.has_data ? "Connected. Waiting for fresh sync metadata." : "Connected but waiting for first data sync.";
    }

    return row.has_data
      ? `Last synced ${row.stale_days} day(s) ago.`
      : `Connected, no rows in selected range. Last synced ${row.stale_days} day(s) ago.`;
  }

  renderError(message, { source = "dashboard_ui", trigger_source = "unknown", source_status = "error", captureTelemetry = true } = {}) {
    this.errorTarget.textContent = message;
    this.errorTarget.classList.remove("hidden");

    if (!captureTelemetry) {
      return;
    }

    this.captureEvent("analytics_source_error_shown", {
      ...this.currentRangeTelemetry(),
      source,
      source_status,
      trigger_source,
      error_message: message,
      result_status: "shown",
    });
  }

  captureDateRangeChangedIfNeeded(properties = {}) {
    const nextFingerprint = toRangeFingerprint(this.startDateTarget.value, this.endDateTarget.value);
    if (!this.startDateTarget.value || !this.endDateTarget.value || nextFingerprint === this.lastRangeFingerprint) {
      return;
    }

    this.lastRangeFingerprint = nextFingerprint;
    this.captureEvent("analytics_date_range_changed", {
      ...this.currentRangeTelemetry(),
      ...properties,
    });
  }

  currentRangeTelemetry() {
    const startDate = this.startDateTarget.value;
    const endDate = this.endDateTarget.value;

    return {
      project_id: this.projectIdValue,
      date_range_start: startDate,
      date_range_end: endDate,
      range_days: this.rangeDays(startDate, endDate),
    };
  }

  rangeDays(startDate, endDate) {
    if (!startDate || !endDate) {
      return null;
    }

    const start = new Date(startDate);
    const end = new Date(endDate);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
      return null;
    }

    if (end.getTime() < start.getTime()) {
      return null;
    }

    return Math.floor((end.getTime() - start.getTime()) / DAY) + 1;
  }

  captureEvent(eventName, properties = {}) {
    if (!window.posthog || typeof window.posthog.capture !== "function") {
      return;
    }
    window.posthog.capture(eventName, properties);
  }

  currentPreset() {
    const active = this.presetTargets.find((target) => target.dataset.active === "true");
    return active ? active.dataset.days : "30";
  }

  setPresetUi(selected) {
    this.presetTargets.forEach((target) => {
      const isSelected = target.dataset.days === selected;
      target.dataset.active = isSelected ? "true" : "false";
      target.classList.toggle("bg-gray-900", isSelected);
      target.classList.toggle("text-white", isSelected);
      target.classList.toggle("text-gray-700", !isSelected);
      target.classList.toggle("bg-gray-100", !isSelected);
    });
  }

  rangeFromPreset(days) {
    const numericDays = Number(days || 30);
    const end = new Date();
    const start = new Date(end.getTime() - (numericDays - 1) * DAY);
    return {
      start: toLocalIsoDate(start),
      end: toLocalIsoDate(end),
    };
  }
}
