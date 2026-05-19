import { Controller } from "@hotwired/stimulus";
import { showMessage } from "../utils/messages";

export default class extends Controller {
  static targets = [
    "progress",
    "error",
    "detailsSpinner",
    "detailsCheck",
    "detailsCross",
    "suggestionsSpinner",
    "suggestionsCheck",
    "suggestionsCross",
    "resultsButton",
    "projectsList",
    "backgroundTasks",
    "addButton",
    "form"
  ];

  toggleForm() {
    this.addButtonTarget.classList.toggle('hidden');
    this.formTarget.classList.toggle('hidden');
  }

  async handleSubmit(event) {
    event.preventDefault();
    this.initializeUI();

    const formData = new FormData(event.target);
    const url = formData.get('url');

    try {
      // Perform scan first
      const scanData = await this.performScan(url);

      // Add project to list and update results button immediately after successful scan
      this.addProjectToList(scanData);
      this.updateResultsButton(scanData.project_id);
    } catch (scanError) {
      this.handleScanError(scanError);
    }
  }

  initializeUI() {
    if (this.hasErrorTarget) {
      this.errorTarget.classList.add('hidden');
    }
    this.progressTarget.classList.remove('hidden');
  }

  async performScan(url) {
    const scanResponse = await fetch('/api/projects/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': this.getCSRFToken(),
      },
      body: JSON.stringify({ url, source: "scan_project" })
    });

    if (!scanResponse.ok) {
      throw new Error('Failed to scan URL');
    }

    const scanData = await scanResponse.json();
    if (scanData.status === 'error') {
      throw new Error(scanData.message);
    }

    // Update UI after successful scan
    this.detailsSpinnerTarget.classList.add('hidden');
    this.detailsCheckTarget.classList.remove('hidden');
    // Show background tasks message
    if (this.hasBackgroundTasksTarget) {
      const el = this.backgroundTasksTarget;
      el.classList.remove('hidden');
      // Force reflow to ensure transition
      void el.offsetWidth;
      el.classList.remove('translate-x-full', 'opacity-0', 'pointer-events-none');
      el.classList.add('translate-x-0', 'opacity-100', 'pointer-events-auto');
    }

    return scanData;
  }

  async generateSuggestions(projectId, contentType="SHARING") {
    const suggestionsResponse = await fetch('/api/generate-title-suggestions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': this.getCSRFToken(),
      },
      body: JSON.stringify({
        project_id: projectId,
        content_type: contentType
      })
    });

    if (!suggestionsResponse.ok) {
      throw new Error('Failed to generate title suggestions');
    }

    const suggestionsData = await suggestionsResponse.json();
    if (suggestionsData.status === "error") {
      throw new Error(suggestionsData.message);
    }

    return suggestionsData;
  }

  handleScanError(error) {
    this.detailsSpinnerTarget.classList.add('hidden');
    this.detailsCheckTarget.classList.add('hidden');
    this.detailsCrossTarget.classList.remove('hidden');
    showMessage(error.message || "Failed to scan URL", 'error');
  }

  handleSuggestionsError(error) {
    this.suggestionsCheckTarget.classList.add('hidden');
    this.suggestionsSpinnerTarget.classList.add('hidden');
    this.suggestionsCrossTarget.classList.remove('hidden');
    showMessage(error.message || "Failed to generate suggestions", 'error');
  }

  addProjectToList(scanData) {
    const projectElement = this.createProjectElement(scanData);
    if (this.hasProjectsListTarget) {
      this.projectsListTarget.insertBefore(projectElement, this.projectsListTarget.firstChild);
    } else {
      this.projectsListTarget.appendChild(projectElement);
    }

    const emptyState = document.querySelector('[data-empty-state]');
    if (emptyState) {
      emptyState.remove();
    }

    setTimeout(() => {
      projectElement.classList.remove('translate-x-full', 'opacity-0');
    }, 100);
  }

  updateResultsButton(projectId) {
    this.resultsButtonTarget.href = `/project/${projectId}/home/`;
    this.resultsButtonTarget.classList.remove('hidden');
  }

  getCSRFToken() {
    return document.querySelector('[name=csrfmiddlewaretoken]').value;
  }

  createProjectElement(data) {
    const element = document.createElement('div');
    element.className = 'overflow-hidden p-6 bg-white rounded-xl border border-gray-200 shadow-sm transition-all duration-300 transform hover:shadow-lg hover:border-gray-300 hover:-translate-y-1';
    element.dataset.projectSearchTarget = 'item';
    element.dataset.projectSearchText = (data.name || data.url || '').toLowerCase();

    element.innerHTML = `
      <div class="flex flex-col h-full">
        <!-- Header -->
        <div class="flex flex-col gap-3 items-start mb-4 md:flex-row md:items-center md:justify-between">
          <div class="flex flex-col flex-1 gap-2 min-w-0">
            <div class="flex gap-3 items-center">
              <h3 class="text-xl font-bold text-gray-900 truncate">
                ${data.name || data.url}
              </h3>
              <span class="px-2.5 py-1 text-xs font-medium text-gray-700 whitespace-nowrap bg-gray-100 rounded-full ring-1 ring-inset ring-gray-300">
                ${data.type}
              </span>
            </div>

            ${data.url ? `
              <a href="${data.url}" target="_blank" rel="noopener noreferrer"
                 class="text-sm text-gray-500 truncate hover:text-gray-700 max-w-fit">
                ${data.url.length > 50 ? data.url.substring(0, 47) + '...' : data.url} ↗
              </a>
            ` : ''}
          </div>
        </div>

        ${data.summary ? `<p class="mb-4 text-sm leading-relaxed text-gray-600 line-clamp-3">${data.summary}</p>` : ''}

        <!-- Stats Grid -->
        <div class="grid grid-cols-3 gap-3 mb-6">
          <!-- Blog Post Title Suggestions -->
          <div class="p-3 text-center bg-blue-50 rounded-lg border border-blue-100">
            <div class="text-lg font-bold text-blue-900">0</div>
            <div class="text-xs font-medium text-blue-700">Title Ideas</div>
          </div>

          <!-- Generated Blog Posts -->
          <div class="p-3 text-center bg-green-50 rounded-lg border border-green-100">
            <div class="text-lg font-bold text-green-900">0</div>
            <div class="text-xs font-medium text-green-700">Generated</div>
          </div>

          <!-- Posted Blog Posts -->
          <div class="p-3 text-center bg-purple-50 rounded-lg border border-purple-100">
            <div class="text-lg font-bold text-purple-900">0</div>
            <div class="text-xs font-medium text-purple-700">Posted</div>
          </div>
        </div>

        <!-- Action Button (pushed to bottom) -->
        <div class="flex justify-end mt-auto">
          <a href="/project/${data.project_id}/"
             class="px-4 py-2 text-sm font-medium text-white bg-gray-800 rounded-md shadow-sm transition-all duration-200 hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-500">
            View details →
          </a>
        </div>
      </div>
    `;
    return element;
  }
}
