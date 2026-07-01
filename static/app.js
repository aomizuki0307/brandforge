// Progressive enhancement for the BrandForge forms. The page already works as
// plain HTML; this just turns the Brand Kit / Generate forms into fetch calls
// so the gallery can refresh in place. HTTP Basic credentials entered for the
// page are reused by same-origin fetch automatically — no token handling here.

(() => {
  "use strict";

  const csv = (value) =>
    (value || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

  const setStatus = (key, message, state) => {
    const el = document.querySelector(`[data-status="${key}"]`);
    if (!el) return;
    el.textContent = message;
    el.classList.remove("is-ok", "is-err");
    if (state) el.classList.add(state);
  };

  async function postJSON(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      /* non-JSON error body */
    }
    if (!res.ok) {
      const detail = (data && data.detail) || `${res.status} ${res.statusText}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  const withBusy = async (form, fn) => {
    const btn = form.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    try {
      await fn();
    } finally {
      if (btn) btn.disabled = false;
    }
  };

  const kitForm = document.getElementById("kit-form");
  if (kitForm) {
    kitForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const f = kitForm;
      const brand = {
        id: f.id.value.trim(),
        name: f.name.value.trim(),
        tone_words: csv(f.tone_words.value),
        palette: csv(f.palette.value),
        style_prompt: f.style_prompt.value.trim(),
        audience: f.audience.value.trim(),
      };
      withBusy(f, async () => {
        setStatus("kit", "Saving…");
        try {
          const out = await postJSON("/brandkits", brand);
          setStatus("kit", `Saved ${out.brand_kit_key}`, "is-ok");
        } catch (err) {
          setStatus("kit", err.message, "is-err");
        }
      });
    });
  }

  const genForm = document.getElementById("gen-form");
  if (genForm) {
    genForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const f = genForm;
      const brandId = f.brand_kit_id.value.trim();
      // Reuse the Brand Kit panel's details when it describes the same brand,
      // so an inline generate stays on-brand instead of sending a bare kit.
      const kit = document.getElementById("kit-form");
      const sameBrand = kit && kit.id.value.trim() === brandId;
      const brand = {
        id: brandId,
        name: sameBrand && kit.name.value.trim() ? kit.name.value.trim() : brandId,
        tone_words: sameBrand ? csv(kit.tone_words.value) : [],
        palette: sameBrand ? csv(kit.palette.value) : [],
        style_prompt: sameBrand ? kit.style_prompt.value.trim() : "",
        audience: sameBrand ? kit.audience.value.trim() : "",
      };
      const payload = {
        brand,
        campaign: {
          id: f.campaign_id.value.trim(),
          brand_kit_id: brandId,
          theme: f.theme.value.trim(),
          num_variants: Number(f.num_variants.value) || 3,
        },
      };
      withBusy(f, async () => {
        setStatus("gen", "Generating… this makes billable provider calls and can take a minute.");
        try {
          const out = await postJSON("/campaigns", payload);
          setStatus("gen", `Generated ${out.assets.length} asset(s). Refreshing gallery…`, "is-ok");
          window.location.assign(`/?campaign_id=${encodeURIComponent(payload.campaign.id)}`);
        } catch (err) {
          setStatus("gen", err.message, "is-err");
        }
      });
    });
  }
})();
