import { Controller } from "@hotwired/stimulus";
import { showMessage } from "../utils/messages";

export default class extends Controller {
  static values = {
    projectId: Number,
    postTypeId: Number
  };

  static targets = ["suggestionsList", "suggestionsContainer", "activeSuggestionsList"];

  async generate(event) {
    event.preventDefault();
    const button = event.currentTarget;
    const contentType = button.dataset.titleSuggestionsContentTypeValue || "SHARING";

    try {
      // Show loading state
      button.disabled = true;
      button.innerHTML = `
        <div class="flex gap-x-2 items-center">
          <div class="w-4 h-4 rounded-full border-2 border-gray-300 animate-spin border-t-gray-700"></div>
          <span>Generating...</span>
        </div>
      `;

      // Make API call
      const response = await fetch("/api/generate-title-suggestions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
        },
        body: JSON.stringify({
          project_id: this.projectIdValue,
          content_type: contentType,
          post_type_id: this.hasPostTypeIdValue ? this.postTypeIdValue : null
        })
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.message || "Failed to generate suggestions");
      }

      const { suggestions_html, status, message } = await response.json();

      if (status === "error") {
        console.log(message);
        throw new Error(message || "Failed to generate suggestions");
      }

      this.suggestionsContainerTarget.classList.remove("hidden");
      suggestions_html.forEach(html => {
        this.activeSuggestionsListTarget.insertAdjacentHTML("beforeend", html);
      });

      // Restore button state
      button.disabled = false;
      button.innerHTML = `
        <svg class="mr-2 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
        </svg>
        Generate More Ideas
      `;

    } catch (error) {
      button.disabled = false;
      button.innerHTML = `
        <svg class="mr-2 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
        </svg>
        Generate More Ideas
      `;

      showMessage(error.message, "error");
    }
  }
}
