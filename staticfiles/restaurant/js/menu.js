// Customer Menu JavaScript (Database-Driven Architecture)
// Section 14: Hard-coded array completely removed. Data is rendered from PostgreSQL by Django.

document.addEventListener("DOMContentLoaded", () => {
  const itemsContainer = document.querySelector("#items");
  const searchInput = document.querySelector("#search");
  const filtersContainer = document.querySelector("#filters");
  const cartToast = document.querySelector("#cartToast");
  const toastMsg = document.querySelector("#toastMsg");

  if (!itemsContainer) return;

  const itemElements = Array.from(itemsContainer.querySelectorAll(".item"));
  let activeFilter = "all";

  function filterItems() {
    const searchText = searchInput ? searchInput.value.toLowerCase().trim() : "";
    let visibleCount = 0;

    itemElements.forEach((item) => {
      const category = item.dataset.category || "";
      const searchData = item.dataset.search || "";

      const matchesFilter = activeFilter === "all" || category === activeFilter;
      const matchesSearch = !searchText || searchData.includes(searchText);

      if (matchesFilter && matchesSearch) {
        item.style.display = "";
        visibleCount++;
      } else {
        item.style.display = "none";
      }
    });

    let noMatchBox = document.querySelector("#noMatchBox");
    if (visibleCount === 0) {
      if (!noMatchBox) {
        noMatchBox = document.createElement("div");
        noMatchBox.id = "noMatchBox";
        noMatchBox.style.cssText =
          "grid-column: 1 / -1; padding: 48px 24px; text-align: center; background: rgba(8, 22, 44, 0.4); border: 1px dashed rgba(121, 202, 255, 0.2); border-radius: 16px;";
        itemsContainer.appendChild(noMatchBox);
      }
      noMatchBox.innerHTML = `<p style="color: var(--text-muted); font-size: 15px; margin: 0;">No culinary creations match your search for "${searchText}".</p>`;
    } else if (noMatchBox) {
      noMatchBox.remove();
    }
  }

  if (searchInput) {
    searchInput.addEventListener("input", filterItems);
  }

  if (filtersContainer) {
    filtersContainer.addEventListener("click", (event) => {
      const selectedButton = event.target.closest("button");
      if (!selectedButton || !selectedButton.dataset.f) return;

      filtersContainer.querySelectorAll("button").forEach((btn) => btn.classList.remove("active"));
      selectedButton.classList.add("active");
      activeFilter = selectedButton.dataset.f;
      filterItems();
    });
  }

  // Toast notification for Add to Cart
  window.handleAddToCart = function (itemId, itemName, itemPrice) {
    // Save to local cart session
    let cart = [];
    try {
      cart = JSON.parse(sessionStorage.getItem("fw_cart") || "[]");
    } catch (e) {
      cart = [];
    }

    const existing = cart.find((i) => i.item_id === itemId);
    if (existing) {
      existing.quantity += 1;
    } else {
      cart.push({ item_id: itemId, name: itemName, price: itemPrice, quantity: 1 });
    }
    sessionStorage.setItem("fw_cart", JSON.stringify(cart));

    if (cartToast && toastMsg) {
      toastMsg.textContent = `Added "${itemName}" (₹${itemPrice}) to your dining selection.`;
      cartToast.style.display = "flex";
      setTimeout(() => {
        cartToast.style.display = "none";
      }, 3500);
    }
  };
});
