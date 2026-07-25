import "@testing-library/jest-dom/vitest";

beforeEach(() => {
  window.localStorage.clear();
  vi.stubEnv("VITE_DEMO_MODE", "true");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});
