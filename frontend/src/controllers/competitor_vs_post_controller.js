import { Controller } from "@hotwired/stimulus";
import { showMessage } from "../utils/messages";

export default class extends Controller {
  static values = {
    competitorId: Number,
    projectId: Number,
    generationInProgress: Boolean
  };

  static targets = ["buttonContainer"];

  connect() {
    this.pollingTimeoutId = null;

    if (this.generationInProgressValue) {
      this._setGeneratingState();
      this._pollGenerationStatus();
    }
  }

  disconnect() {
    if (this.pollingTimeoutId) {
      clearTimeout(this.pollingTimeoutId);
      this.pollingTimeoutId = null;
    }
  }

  async generatePost(event) {
    event.preventDefault();

    try {
      this._setGeneratingState();

      const response = await fetch("/api/generate-competitor-vs-title", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
        },
        body: JSON.stringify({
          competitor_id: this.competitorIdValue
        })
      });

      const data = await response.json();

      if (!response.ok || data.status === "error") {
        throw new Error(data.message || "Failed to generate competitor comparison post");
      }

      if (data.status === "success") {
        this._setCompletedState();
        showMessage("VS blog post is ready.", "success");
        return;
      }

      if (data.status === "processing") {
        showMessage(
          data.message || "Generation started. This can take a few minutes.",
          "success"
        );
        this._pollGenerationStatus();
        return;
      }

      throw new Error("Unexpected response while generating competitor post");
    } catch (error) {
      this._resetToInitialState();
      showMessage(error.message || "Failed to generate competitor post", "error");
    }
  }

  async _pollGenerationStatus() {
    const pollInterval = 4000;
    const maxAttempts = 90; // ~6 minutes
    let attempts = 0;

    const poll = async () => {
      attempts++;

      try {
        const response = await fetch(
          `/api/competitor-post-generation-status/${this.competitorIdValue}`,
          {
            headers: {
              "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
            }
          }
        );

        if (!response.ok) {
          throw new Error("Failed to check competitor post generation status");
        }

        const data = await response.json();

        if (data.status === "completed") {
          this._setCompletedState(data.view_post_url);
          showMessage("VS blog post generated successfully!", "success");
          return;
        }

        if (data.status === "failed") {
          throw new Error(data.message || "Competitor post generation failed");
        }

        if (data.status === "processing") {
          if (attempts >= maxAttempts) {
            throw new Error(
              "Generation is taking longer than expected. Please refresh in a few minutes."
            );
          }

          this.pollingTimeoutId = setTimeout(poll, pollInterval);
          return;
        }

        // idle or unexpected state
        this._resetToInitialState();
      } catch (error) {
        this._resetToInitialState();
        showMessage(error.message || "Failed to check generation status", "error");
      }
    };

    this.pollingTimeoutId = setTimeout(poll, 2000);
  }

  _setGeneratingState() {
    this.buttonContainerTarget.innerHTML = `
      <button
        disabled
        class="inline-flex items-center px-3 py-1.5 text-xs font-medium text-white bg-gray-900 rounded-md border border-gray-900 opacity-75 cursor-not-allowed"
        title="Competitor comparison post generation is in progress">
        <svg class="mr-1.5 w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
        </svg>
        Generating...
      </button>
    `;
  }

  _setCompletedState(viewPostUrl = null) {
    const resolvedUrl = viewPostUrl || `/project/${this.projectIdValue}/competitor/${this.competitorIdValue}/post/`;

    this.buttonContainerTarget.innerHTML = `
      <a href="${resolvedUrl}"
         class="inline-flex items-center px-3 py-1.5 text-xs font-medium text-white bg-gray-900 rounded-md border border-gray-900 hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-gray-500">
        <svg class="mr-1.5 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path>
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"></path>
        </svg>
        View Post
      </a>
    `;
  }

  _resetToInitialState() {
    this.buttonContainerTarget.innerHTML = `
      <button
        data-action="competitor-vs-post#generatePost"
        class="inline-flex items-center px-3 py-1.5 text-xs font-medium text-white bg-gray-900 rounded-md border border-gray-900 hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-gray-500">
        <svg class="mr-1.5 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path>
        </svg>
        Generate Post
      </button>
    `;
  }
}
