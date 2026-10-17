"use strict";

document.getElementById("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fel = document.getElementById("loginError");
  const knapp = e.target.querySelector("button");
  fel.hidden = true;
  knapp.disabled = true;
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Studio": "1" },
      body: JSON.stringify({
        anvandare: document.getElementById("user").value.trim(),
        losenord: document.getElementById("password").value,
      }),
    });
    if (res.ok) {
      location.replace("/");
      return;
    }
    const data = await res.json().catch(() => ({}));
    fel.textContent = data.detail || "Inloggningen misslyckades.";
    fel.hidden = false;
  } catch {
    fel.textContent = "Kunde inte nå servern.";
    fel.hidden = false;
  } finally {
    knapp.disabled = false;
  }
});
