import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = ["query", "list", "item", "emptyState", "searchContainer"];

  connect() {
    this.currentQuery = "";
    this.observer = new MutationObserver(() => {
      this.rebuildIndex();
      this.applyFilter(this.currentQuery);
    });

    if (this.hasListTarget) {
      this.observer.observe(this.listTarget, { childList: true });
    }

    this.rebuildIndex();
    this.applyFilter("");
  }

  disconnect() {
    if (this.observer) {
      this.observer.disconnect();
    }
  }

  filter() {
    const query = this.hasQueryTarget ? this.queryTarget.value : "";
    this.applyFilter(query);
  }

  rebuildIndex() {
    this.index = this.itemTargets.map((item) => ({
      element: item,
      text: (item.dataset.projectSearchText || item.textContent || "").toLowerCase(),
    }));
  }

  applyFilter(rawQuery) {
    const query = (rawQuery || "").trim().toLowerCase();
    this.currentQuery = query;

    let visibleCount = 0;

    this.index.forEach(({ element, text }) => {
      const matches = query === "" || text.includes(query);
      element.classList.toggle("hidden", !matches);
      if (matches) visibleCount += 1;
    });

    if (this.hasEmptyStateTarget) {
      const shouldShow = this.index.length > 0 && visibleCount === 0;
      this.emptyStateTarget.classList.toggle("hidden", !shouldShow);
    }

    if (this.hasSearchContainerTarget) {
      this.searchContainerTarget.classList.toggle("hidden", this.index.length === 0);
    }
  }
}
