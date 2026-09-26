document.addEventListener("DOMContentLoaded", () => {
  const languageSelect = document.getElementById("target_language") as HTMLSelectElement | null;
  const levelSelect = document.getElementById("cefr_level") as HTMLSelectElement | null;
  const form = document.getElementById("vocab-form") as HTMLFormElement | null;

  const STORAGE_KEY_LANG = "vocabcatcher_target_lang";
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
    form.addEventListener("submit", () => {
      if (languageSelect) localStorage.setItem(STORAGE_KEY_LANG, languageSelect.value);
      if (levelSelect) localStorage.setItem(STORAGE_KEY_LEVEL, levelSelect.value);
    });
  }
});
