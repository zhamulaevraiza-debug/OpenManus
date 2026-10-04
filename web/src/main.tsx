import "./styles/index.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { shouldRetry } from "@/api/queries";
import { ToastProvider } from "@/components/ToastProvider";
import { I18nProvider } from "@/i18n/I18nProvider";
import { initInstallPrompt, registerServiceWorker } from "@/pwa";
import { ThemeProvider } from "@/theme/ThemeProvider";

import { App } from "./App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: shouldRetry, refetchOnWindowFocus: true },
    mutations: { retry: false },
  },
});

initInstallPrompt();
registerServiceWorker();

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <ThemeProvider>
          <ToastProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </ToastProvider>
        </ThemeProvider>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
);
