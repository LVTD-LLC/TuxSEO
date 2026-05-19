import { Controller } from "@hotwired/stimulus";
import * as d3 from "d3";
import { showMessage } from "../utils/messages";

// Controller to handle keyword hover functionality with summary stats
export default class extends Controller {
  static values = { text: String, projectId: Number };

  connect() {
    this.tooltip = null;
    this.keywordData = null;
    this.isLoading = false;
    this.originalClasses = this.element.className;
    this.isPointerInsideElement = false;
    this.isElementFocused = false;
    this.escapeKeyListener = this.handleDocumentKeydown.bind(this);
    this.windowMouseOutListener = this.handleWindowMouseOut.bind(this);

    // Store initial styling state
    this.initialInUse = this.element.classList.contains("text-green-700");

    document.addEventListener("keydown", this.escapeKeyListener);
    window.addEventListener("mouseout", this.windowMouseOutListener);
  }

  disconnect() {
    document.removeEventListener("keydown", this.escapeKeyListener);
    window.removeEventListener("mouseout", this.windowMouseOutListener);
    this.hideTooltip();
  }

  async mouseenter(event) {
    this.isPointerInsideElement = true;

    // Prevent multiple simultaneous requests
    if (this.isLoading) return;

    // Show loading tooltip immediately
    this.showLoadingTooltip(event);

    // Fetch keyword data if not already cached
    if (!this.keywordData) {
      await this.fetchKeywordData();
    }

    if (!this.isTooltipActive()) {
      this.hideTooltip();
      return;
    }

    // Update tooltip with actual data
    this.showDataTooltip(event);
  }

  mouseleave() {
    this.isPointerInsideElement = false;
    if (!this.isElementFocused) {
      this.hideTooltip();
    }
  }

  async focus(event) {
    this.isElementFocused = true;

    if (!this.keywordData) {
      await this.fetchKeywordData();
    }

    if (!this.isTooltipActive()) {
      this.hideTooltip();
      return;
    }

    this.showDataTooltip(this.getElementPositionEvent(event));
  }

  blur() {
    this.isElementFocused = false;
    if (!this.isPointerInsideElement) {
      this.hideTooltip();
    }
  }

  async keydown(event) {
    const isEnterKey = event.key === "Enter";
    const isSpaceKey = event.key === " ";
    const isEscapeKey = event.key === "Escape";

    if (isEscapeKey) {
      event.preventDefault();
      this.hideTooltip();
      this.element.blur();
      return;
    }

    if (!isEnterKey && !isSpaceKey) return;

    event.preventDefault();

    if (!this.keywordData) {
      await this.fetchKeywordData();
    }

    await this.handleClick(event);
    this.hideTooltip();
  }

  handleDocumentKeydown(event) {
    if (event.key !== "Escape") return;

    this.hideTooltip();
  }

  async handleClick(event) {
    if (!this.keywordData) return;

    event.preventDefault();

    if (!this.keywordData.is_in_project) {
      // Save keyword to project
      await this.saveKeyword();
    } else {
      // Toggle keyword usage
      await this.toggleKeywordUsage();
    }
  }

  handleWindowMouseOut(event) {
    if (event.relatedTarget) return;

    this.isPointerInsideElement = false;
    if (!this.isElementFocused) {
      this.hideTooltip();
    }
  }

  showLoadingTooltip(event) {
    this.hideTooltip(); // Remove any existing tooltip

    this.tooltip = document.createElement("div");
    this.tooltip.className = "keyword-tooltip";
    this.tooltip.innerHTML = `
      <div class="px-3 py-2 max-w-xs text-xs text-white bg-gray-900 rounded-lg shadow-lg">
        <div class="flex items-center space-x-2">
          <div class="w-3 h-3 rounded-full border-2 border-white animate-spin border-t-transparent"></div>
          <span>Loading keyword data...</span>
        </div>
      </div>
    `;

    this.positionTooltip(event);
    document.body.appendChild(this.tooltip);
  }

  showDataTooltip(event) {
    if (!this.keywordData) return;

    this.hideTooltip(); // Remove loading tooltip

    this.tooltip = document.createElement("div");
    this.tooltip.className = "keyword-tooltip";

    const { keyword_text, volume, cpc_value, cpc_currency, competition, is_in_project, in_use, trend_data } = this.keywordData;

    // Format numbers for display
    const formatVolume = (vol) => {
      if (!vol) return "N/A";
      if (vol >= 1000000) return `${(vol / 1000000).toFixed(1)}M`;
      if (vol >= 1000) return `${(vol / 1000).toFixed(1)}K`;
      return vol.toString();
    };

    const formatCPC = (value, currency) => {
      if (!value) return "N/A";
      const symbol = currency === "usd" ? "$" : currency?.toUpperCase() || "";
      return `${symbol}${value.toFixed(2)}`;
    };

    const formatCompetition = (comp) => {
      if (comp === null || comp === undefined) return "N/A";
      return `${(comp * 100).toFixed(0)}%`;
    };

    // Show different content based on whether keyword is in project
    if (!is_in_project) {
      this.tooltip.classList.add("cursor-pointer");
      this.tooltip.innerHTML = `
        <div class="px-4 py-3 max-w-sm text-xs text-white bg-gray-900 rounded-lg shadow-lg transition-colors cursor-pointer hover:bg-gray-800">
          <div class="mb-2 text-sm font-semibold text-blue-200">${keyword_text}</div>
          <div class="py-2 text-center">
            <div class="mb-1 font-medium text-yellow-300">Click to Save Keyword</div>
            <div class="text-xs text-gray-400">Add this keyword to your project</div>
          </div>
        </div>
      `;

      // Add click handler to save keyword
      this.tooltip.addEventListener("click", () => this.saveKeyword());
    } else {
      // Create main content
      let tooltipContent = `
        <div class="px-4 py-3 max-w-sm text-xs text-white bg-gray-900 rounded-lg shadow-lg transition-colors cursor-pointer hover:bg-gray-800">
          <div class="mb-2 text-sm font-semibold text-blue-200">${keyword_text}</div>
          <div class="space-y-1.5">
            <div class="flex justify-between">
              <span class="text-gray-300">Search Volume:</span>
              <span class="font-medium">${formatVolume(volume)}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-300">CPC:</span>
              <span class="font-medium">${formatCPC(cpc_value, cpc_currency)}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-300">Competition:</span>
              <span class="font-medium">${formatCompetition(competition)}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-300">In Use:</span>
              <span class="font-medium ${in_use ? 'text-green-400' : 'text-gray-400'}">${in_use ? 'Yes' : 'No'}</span>
            </div>
      `;

      // Add trend chart if data exists
      if (trend_data && trend_data.length > 0) {
        tooltipContent += `
            <div class="pt-2 mt-3 border-t border-gray-700">
              <div class="mb-2 text-xs text-gray-300">Search Trend</div>
              <div id="trend-chart-${this.keywordData.id}" class="w-full h-12"></div>
            </div>
        `;
      }

      tooltipContent += `
          </div>
          <div class="pt-2 mt-3 text-center border-t border-gray-600">
            <div class="text-xs text-gray-400">
              ${in_use ? 'Click to stop using' : 'Click to start using for blog posts'}
            </div>
          </div>
        </div>
      `;

      this.tooltip.innerHTML = tooltipContent;
      this.tooltip.classList.add("cursor-pointer");

      // Add click handler to toggle keyword usage
      this.tooltip.addEventListener("click", () => this.toggleKeywordUsage());

      // Render trend chart if data exists
      if (trend_data && trend_data.length > 0) {
        setTimeout(() => {
          this.renderTrendChart(`trend-chart-${this.keywordData.id}`, trend_data);
        }, 10);
      }
    }

    this.positionTooltip(event);
    document.body.appendChild(this.tooltip);
  }

  positionTooltip(event) {
    if (!this.tooltip) return;

    const tooltipRect = this.tooltip.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let left = event.pageX + 10;
    let top = event.pageY - 10;

    // Adjust horizontal position if tooltip would go off-screen
    if (left + tooltipRect.width > viewportWidth) {
      left = event.pageX - tooltipRect.width - 10;
    }

    // Adjust vertical position if tooltip would go off-screen
    if (top + tooltipRect.height > viewportHeight) {
      top = event.pageY - tooltipRect.height - 10;
    }

    // Ensure tooltip doesn't go above viewport
    if (top < 0) {
      top = event.pageY + 20;
    }

    this.tooltip.style.position = "absolute";
    this.tooltip.style.left = `${left}px`;
    this.tooltip.style.top = `${top}px`;
    this.tooltip.style.zIndex = "1000";
    this.tooltip.style.pointerEvents = "none";
  }

  hideTooltip() {
    if (this.tooltip) {
      this.tooltip.remove();
      this.tooltip = null;
    }
  }

  async fetchKeywordData() {
    if (this.isLoading) return;

    this.isLoading = true;

    try {
      const response = await fetch(`/api/keywords/details?keyword_text=${encodeURIComponent(this.textValue)}&project_id=${this.projectIdValue}`, {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": this.getCSRFToken(),
        },
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();

      if (data.status === "success") {
        this.keywordData = data.keyword;
        this.updateChipStyling();
      } else {
        console.warn("Failed to fetch keyword data:", data.message);
        this.keywordData = {
          keyword_text: this.textValue,
          volume: null,
          cpc_value: null,
          cpc_currency: null,
          competition: null,
          is_in_project: false,
          in_use: false,
          project_keyword_id: null,
          trend_data: null
        };
      }
    } catch (error) {
      console.error("Error fetching keyword data:", error);
      // Set fallback data
      this.keywordData = {
        keyword_text: this.textValue,
        volume: null,
        cpc_value: null,
        cpc_currency: null,
        competition: null,
        is_in_project: false,
        in_use: false,
        project_keyword_id: null,
        trend_data: null
      };
    } finally {
      this.isLoading = false;
    }
  }

  renderTrendChart(containerId, trendData) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const { width, height } = container.getBoundingClientRect();
    const padding = { top: 2, right: 2, bottom: 2, left: 2 };

    const svg = d3.select(container)
      .append("svg")
      .attr("width", width)
      .attr("height", height);

    // X Scale
    const xScale = d3.scaleLinear()
      .domain([0, trendData.length - 1])
      .range([padding.left, width - padding.right]);

    // Y Scale
    let yMin = d3.min(trendData, d => d.value);
    let yMax = d3.max(trendData, d => d.value);

    // Handle edge cases
    if (yMin === yMax) {
      yMin = yMin > 0 ? yMin * 0.9 : yMin * 1.1;
      yMax = yMax > 0 ? yMax * 1.1 : yMax * 0.9;
    }

    const yScale = d3.scaleLinear()
      .domain([yMin, yMax])
      .range([height - padding.bottom, padding.top]);

    // Create line generator
    const line = d3.line()
      .x((d, i) => xScale(i))
      .y(d => yScale(d.value))
      .curve(d3.curveMonotoneX);

    // Create area generator
    const area = d3.area()
      .x((d, i) => xScale(i))
      .y0(height - padding.bottom)
      .y1(d => yScale(d.value))
      .curve(d3.curveMonotoneX);

    // Add area fill
    svg.append("path")
      .datum(trendData)
      .attr("class", "area")
      .attr("d", area)
      .attr("fill", "#60A5FA")
      .attr("fill-opacity", 0.2);

    // Add line
    svg.append("path")
      .datum(trendData)
      .attr("class", "line")
      .attr("d", line)
      .attr("fill", "none")
      .attr("stroke", "#60A5FA")
      .attr("stroke-width", 1.5);
  }

  async saveKeyword() {
    if (!this.keywordData || this.keywordData.is_in_project) return;

    try {
      const response = await fetch("/api/keywords/add", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": this.getCSRFToken(),
        },
        body: JSON.stringify({
          project_id: this.projectIdValue,
          keyword_text: this.textValue.trim()
        })
      });

      const data = await response.json();

      if (data.status === "success") {
        showMessage("Keyword added to project successfully!", "success");
        // Update the cached data to reflect that it's now in the project
        this.keywordData.is_in_project = true;
        this.keywordData.in_use = false; // New keywords start as not in use
        this.keywordData.project_keyword_id = data.keyword.id;
        this.updateChipStyling();
        this.hideTooltip();
      } else {
        showMessage(data.message || "Failed to add keyword to project.", "error");
      }
    } catch (error) {
      console.error("Error saving keyword:", error);
      showMessage("An error occurred while saving the keyword.", "error");
    }
  }

  async toggleKeywordUsage() {
    if (!this.keywordData || !this.keywordData.is_in_project || !this.keywordData.project_keyword_id) return;

    try {
      const response = await fetch("/api/keywords/toggle-use", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": this.getCSRFToken(),
        },
        body: JSON.stringify({
          project_id: this.projectIdValue,
          keyword_id: this.keywordData.id
        })
      });

      const data = await response.json();

      if (data.status === "success") {
        this.keywordData.in_use = data.use;
        this.updateChipStyling();

        const message = data.use
          ? "Keyword is now in use for blog posts!"
          : "Keyword usage disabled.";
        showMessage(message, "success");

        this.hideTooltip();
      } else {
        showMessage(data.message || "Failed to toggle keyword usage.", "error");
      }
    } catch (error) {
      console.error("Error toggling keyword usage:", error);
      showMessage("An error occurred while updating keyword usage.", "error");
    }
  }

  updateChipStyling() {
    if (!this.keywordData) return;

    // Apply green accent if keyword is in use
    if (this.keywordData.in_use) {
      this.element.classList.remove("text-gray-600", "bg-gray-50", "border-gray-200", "hover:bg-gray-100");
      this.element.classList.add("text-green-700", "bg-green-50", "border-green-200", "hover:bg-green-100");
    } else {
      this.element.classList.remove("text-green-700", "bg-green-50", "border-green-200", "hover:bg-green-100");
      this.element.classList.add("text-gray-600", "bg-gray-50", "border-gray-200", "hover:bg-gray-100");
    }
  }

  getCSRFToken() {
    // Try to get CSRF token from cookie (Django default)
    const name = "csrftoken";
    const cookies = document.cookie.split(";");
    for (let i = 0; i < cookies.length; i++) {
      let cookie = cookies[i].trim();
      if (cookie.startsWith(name + "=")) {
        return decodeURIComponent(cookie.substring(name.length + 1));
      }
    }
    return "";
  }

  isTooltipActive() {
    return this.isPointerInsideElement || this.isElementFocused;
  }

  getElementPositionEvent(event) {
    if (event && typeof event.pageX === "number" && typeof event.pageY === "number") {
      return event;
    }

    const elementRect = this.element.getBoundingClientRect();
    const elementCenterX = elementRect.left + (elementRect.width / 2) + window.scrollX;
    const elementBottomY = elementRect.bottom + window.scrollY;

    return {
      pageX: elementCenterX,
      pageY: elementBottomY
    };
  }
}
