// SCT Sanity & Control Tool - Client JavaScript
// Minimal vanilla JS for dashboard interactivity

document.addEventListener("DOMContentLoaded", function() {
  // Close any open modal
  const closeButtons = document.querySelectorAll(".modal .actions .btn-secondary");
  closeButtons.forEach(function(btn) {
    btn.addEventListener("click", function() {
      const modal = btn.closest(".modal");
      if (modal) modal.classList.add("hidden");
    });
  });

  // Confirm destructive actions
  document.querySelectorAll("a[data-confirm]").forEach(function(link) {
    link.addEventListener("click", function(e) {
      if (!confirm(link.getAttribute("data-confirm"))) {
        e.preventDefault();
      }
    });
  });

  // Auto-hide flash messages after 5 seconds
  const flashes = document.querySelectorAll(".flash");
  flashes.forEach(function(flash) {
    setTimeout(function() {
      flash.classList.add("hidden");
    }, 5000);
  });
});
