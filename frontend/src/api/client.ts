import axios from "axios";

function normalizeApiBaseUrl(value: string | undefined): string {
  const trimmed = value?.trim();
  if (!trimmed) return "/api";

  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
    return trimmed;
  }

  if (trimmed.startsWith("/")) return trimmed;

  return `/${trimmed}`;
}

export const apiClient = axios.create({
  baseURL: normalizeApiBaseUrl(import.meta.env.VITE_API_URL),
  headers: {
    "Content-Type": "application/json",
  },
  withCredentials: true,
});

let rateLimitToastShown = false;

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;

    if (status === 401) {
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }

    if (status === 429) {
      if (!rateLimitToastShown) {
        rateLimitToastShown = true;
        window.dispatchEvent(
          new CustomEvent("app-rate-limit", {
            detail: { message: "Too many requests. Please wait a moment." },
          })
        );
        setTimeout(() => {
          rateLimitToastShown = false;
        }, 5000);
      }
    }

    return Promise.reject(error);
  }
);

export default apiClient;
