import "@testing-library/jest-dom/vitest";

Object.defineProperty(URL, "createObjectURL", {
  configurable: true,
  value: vi.fn(() => "blob:mediaflow-preview"),
});

Object.defineProperty(URL, "revokeObjectURL", {
  configurable: true,
  value: vi.fn(),
});

beforeEach(() => {
  window.localStorage.clear();
  vi.stubEnv("VITE_DEMO_MODE", "true");
  URL.createObjectURL.mockClear();
  URL.revokeObjectURL.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});
