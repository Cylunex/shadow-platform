function cookie(name) {
  const prefix = `${name}=`;
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

document.addEventListener("click", async (event) => {
  const logout = event.target.closest("[data-logout]");
  if (logout) {
    const response = await fetch("/logout", {
      method: "POST",
      headers: {"X-CSRF-Token": cookie("__Host-shadow-notify-csrf")},
    });
    if (response.ok) window.location.assign("/");
    return;
  }
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const card = button.closest("[data-id]");
  button.disabled = true;
  const response = await fetch(`/v1/inbox/${card.dataset.id}/${button.dataset.action}`, {
    method: "POST",
    headers: {"X-CSRF-Token": cookie("__Host-shadow-notify-csrf")},
  });
  if (!response.ok) {
    button.disabled = false;
    return;
  }
  if (button.dataset.action === "archive") card.remove();
  else {
    card.classList.remove("unread");
    button.remove();
  }
});
