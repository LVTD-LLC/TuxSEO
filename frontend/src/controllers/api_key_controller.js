import { Controller } from "@hotwired/stimulus";
import { showMessage } from "../utils/messages";

const MASKED_KEY = "**********";

export default class extends Controller {
  static targets = ["value", "revealButton"];

  static values = {
    fetchUrl: String,
    regenerateUrl: String,
  };

  connect() {
    this.key = null;
    this.isRevealed = false;
    this.mask();
  }

  async toggleReveal(event) {
    event.preventDefault();

    if (this.isRevealed) {
      this.mask();
      return;
    }

    const key = await this.ensureKey();
    if (!key) {
      return;
    }

    this.valueTarget.value = key;
    this.isRevealed = true;
    this.revealButtonTarget.textContent = "Hide";
  }

  async copy(event) {
    event.preventDefault();

    const key = await this.ensureKey();
    if (!key) {
      return;
    }

    await navigator.clipboard.writeText(key);
    showMessage("API key copied to clipboard.", "success");
  }

  async regenerate(event) {
    event.preventDefault();

    const shouldRegenerate = window.confirm(
      "Regenerate API key? Your current key will stop working immediately."
    );
    if (!shouldRegenerate) {
      return;
    }

    const key = await this.requestKey(this.regenerateUrlValue, { method: "POST" });
    if (!key) {
      return;
    }

    this.key = key;
    this.valueTarget.value = key;
    this.isRevealed = true;
    this.revealButtonTarget.textContent = "Hide";
    showMessage("API key regenerated.", "success");
  }

  async ensureKey() {
    if (this.key) {
      return this.key;
    }

    const key = await this.requestKey(this.fetchUrlValue);
    if (!key) {
      return null;
    }

    this.key = key;
    return key;
  }

  async requestKey(url, options = {}) {
    try {
      const headers = {
        "Content-Type": "application/json",
        ...this.csrfHeader(),
        ...(options.headers || {}),
      };

      const response = await fetch(url, {
        ...options,
        headers,
      });
      const data = await response.json();

      if (!response.ok || data.status !== "success" || !data.key) {
        throw new Error(data.message || "Unable to complete API key request.");
      }

      return data.key;
    } catch (error) {
      showMessage(error.message || "Unable to complete API key request.", "error");
      return null;
    }
  }

  csrfHeader() {
    const token = document.querySelector("[name=csrfmiddlewaretoken]")?.value;
    return token ? { "X-CSRFToken": token } : {};
  }

  mask() {
    this.valueTarget.value = MASKED_KEY;
    this.isRevealed = false;
    this.revealButtonTarget.textContent = "Reveal";
  }
}
