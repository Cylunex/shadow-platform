function cookie(name) {
  const prefix = `${name}=`;
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-retry-delivery]");
  if (!button) return;
  const card = button.closest("[data-delivery-id]");
  button.disabled = true;
  const response = await fetch(
    `/v1/operations/deliveries/${card.dataset.deliveryId}/retry`,
    {
      method: "POST",
      headers: {"X-CSRF-Token": cookie("__Host-shadow-notify-csrf")},
    },
  );
  if (response.ok) card.remove();
  else button.disabled = false;
});
