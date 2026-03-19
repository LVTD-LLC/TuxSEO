import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  open(event) {
    const modalId = event.currentTarget.dataset.deleteModalId;
    if (!modalId) {
      return;
    }

    const modal = this.element.querySelector(`#${CSS.escape(modalId)}`);
    if (modal?.showModal) {
      modal.showModal();
    }
  }

  close(event) {
    const modal = event.currentTarget.closest("dialog");
    modal?.close();
  }

  closeOnBackdrop(event) {
    const modal = event.currentTarget;
    if (event.target === modal) {
      modal.close();
    }
  }
}
