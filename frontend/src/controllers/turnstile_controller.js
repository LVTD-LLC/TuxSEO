import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = ["container", "tokenInput", "status"];
  static values = {
    siteKey: String
  };

  connect() {
    this.hasRendered = false;
    this.scriptLoaded = false;
    this.widgetId = null;

    this.ensureTokenInput();
    this.renderWidget();
  }

  renderWidget() {
    if (this.hasRendered) {
      return;
    }

    if (!this.hasContainerTarget) {
      console.error("Turnstile container not found");
      return;
    }

    if (!this.scriptLoaded) {
      this.loadTurnstileScript();
    } else {
      this.initializeTurnstile();
    }
  }

  loadTurnstileScript() {
    if (document.querySelector('script[src*="turnstile"]')) {
      this.scriptLoaded = true;
      this.waitForTurnstile();
      return;
    }

    const script = document.createElement("script");
    script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    script.onload = () => {
      this.scriptLoaded = true;
      this.waitForTurnstile();
    };
    script.onerror = () => {
      this.setStatus("Verification service failed to load. Please refresh and try again.");
      console.error("Failed to load Turnstile script");
    };
    document.head.appendChild(script);
  }

  waitForTurnstile() {
    const checkTurnstile = () => {
      if (typeof window.turnstile !== "undefined" && window.turnstile.render) {
        this.initializeTurnstile();
      } else {
        setTimeout(checkTurnstile, 50);
      }
    };
    checkTurnstile();
  }

  initializeTurnstile() {
    if (this.hasRendered || !this.hasContainerTarget) {
      return;
    }

    if (typeof window.turnstile === "undefined") {
      this.setStatus("Verification is unavailable right now. Please refresh and retry.");
      console.error("Turnstile not available");
      return;
    }

    if (!this.siteKeyValue) {
      this.setStatus("Verification is unavailable right now. Please contact support.");
      console.error("Turnstile site key not configured");
      return;
    }

    try {
      this.widgetId = window.turnstile.render(this.containerTarget, {
        sitekey: this.siteKeyValue,
        size: "flexible",
        callback: (token) => this.onTokenVerified(token),
        "expired-callback": () => this.onTokenExpired(),
        "error-callback": () => this.onTokenError(),
        "timeout-callback": () => this.onTokenExpired()
      });
      this.hasRendered = true;
      this.setStatus("");
    } catch (error) {
      this.setStatus("Verification failed to initialize. Please refresh and retry.");
      console.error("Error rendering Turnstile:", error);
    }
  }

  ensureTokenInput() {
    if (this.hasTokenInputTarget) {
      return this.tokenInputTarget;
    }

    const hiddenInput = document.createElement("input");
    hiddenInput.type = "hidden";
    hiddenInput.name = "cf-turnstile-response";
    hiddenInput.dataset.turnstileTarget = "tokenInput";
    this.element.appendChild(hiddenInput);
    return hiddenInput;
  }

  onTokenVerified(token) {
    const hiddenInput = this.ensureTokenInput();
    hiddenInput.value = token || "";
    this.setStatus("");
  }

  onTokenExpired() {
    const hiddenInput = this.ensureTokenInput();
    hiddenInput.value = "";
    this.setStatus("Verification expired. Please complete it again before submitting.");

    if (this.widgetId !== null && typeof window.turnstile !== "undefined") {
      window.turnstile.reset(this.widgetId);
    }
  }

  onTokenError() {
    const hiddenInput = this.ensureTokenInput();
    hiddenInput.value = "";
    this.setStatus("Verification failed. Please retry the challenge.");
  }

  setStatus(message) {
    if (!this.hasStatusTarget) {
      return;
    }

    this.statusTarget.textContent = message;
    this.statusTarget.classList.toggle("hidden", !message);
  }
}
