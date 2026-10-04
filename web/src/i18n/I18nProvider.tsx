import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { en } from "./en";
import { detectLanguage, I18nContext, translate, type Dictionary, type I18nValue, type Language } from "./index";
import { ru } from "./ru";

const DICTIONARIES: Record<Language, Dictionary> = { en, ru };

export function I18nProvider({ children, initialLanguage }: { children: ReactNode; initialLanguage?: Language }) {
  const [language, setLanguageState] = useState<Language>(() => initialLanguage ?? detectLanguage());

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const setLanguage = useCallback((next: Language) => {
    setLanguageState(next);
    try {
      localStorage.setItem("om.lang", next);
    } catch {
      // storage unavailable: the choice lasts for this session only
    }
  }, []);

  const value = useMemo<I18nValue>(() => {
    const dictionary = DICTIONARIES[language];
    return {
      language,
      setLanguage,
      t: (key, vars) => translate(dictionary, language, key, vars),
    };
  }, [language, setLanguage]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
