document.addEventListener("DOMContentLoaded", () => {
  const languageSelect = document.getElementById("target_language") as HTMLSelectElement | null;
  const nativeLanguageSelect = document.getElementById("native_language") as HTMLSelectElement | null;
  const levelSelect = document.getElementById("cefr_level") as HTMLSelectElement | null;
  const form = document.getElementById("vocab-form") as HTMLFormElement | null;

  const STORAGE_KEY_LANG = "vocabcatcher_target_lang";
  const STORAGE_KEY_NATIVE_LANG = "vocabcatcher_native_lang";
  const STORAGE_KEY_LEVEL = "vocabcatcher_cefr_level";

  if (languageSelect) {
    const savedLang = localStorage.getItem(STORAGE_KEY_LANG);
    if (savedLang) {
      languageSelect.value = savedLang;
    }
    languageSelect.addEventListener("change", () => {
      localStorage.setItem(STORAGE_KEY_LANG, languageSelect.value);
    });
  }

  if (nativeLanguageSelect) {
    const savedNativeLang = localStorage.getItem(STORAGE_KEY_NATIVE_LANG);
    if (savedNativeLang) {
      nativeLanguageSelect.value = savedNativeLang;
    }
    nativeLanguageSelect.addEventListener("change", () => {
      localStorage.setItem(STORAGE_KEY_NATIVE_LANG, nativeLanguageSelect.value);
    });
  }

  if (levelSelect) {
    const savedLevel = localStorage.getItem(STORAGE_KEY_LEVEL);
    if (savedLevel) {
      levelSelect.value = savedLevel;
    }
    levelSelect.addEventListener("change", () => {
      localStorage.setItem(STORAGE_KEY_LEVEL, levelSelect.value);
    });
  }

  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();

      if (languageSelect) localStorage.setItem(STORAGE_KEY_LANG, languageSelect.value);
      if (nativeLanguageSelect) localStorage.setItem(STORAGE_KEY_NATIVE_LANG, nativeLanguageSelect.value);
      if (levelSelect) localStorage.setItem(STORAGE_KEY_LEVEL, levelSelect.value);

      const submitBtn = form.querySelector('button[type="submit"]') as HTMLButtonElement | null;
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Submitting...';
      }

      const formData = new FormData(form);

      try {
        const response = await fetch("/api/tasks", {
          method: "POST",
          body: formData,
          redirect: "manual",
        });

        // Handle redirect or status
        let targetUrl = response.headers.get("location");
        if (!targetUrl && (response.status === 303 || response.status === 302 || response.status === 200)) {
          // If browser followed or intercepted, response.url might have it
          if (response.url && response.url.includes("/operation/")) {
            targetUrl = response.url;
          }
        }

        if (targetUrl) {
          window.location.href = targetUrl;
          return;
        }

        // If redirect was manual (opaque 0 or 303 without location accessible via CORS)
        // Check latest operation
        const opsRes = await fetch("/api/operations");
        if (opsRes.ok) {
          const ops = await opsRes.json();
          if (ops.length > 0) {
            window.location.href = `/operation/${ops[0].id}`;
            return;
          }
        }

        window.location.href = "/operations";
      } catch (err: any) {
        alert(`Error starting operation: ${err.message}`);
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.innerText = "Next →";
        }
      }
    });
  }
});
