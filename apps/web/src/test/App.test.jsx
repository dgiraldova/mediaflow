import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import { AuthProvider } from "../state/AuthContext";

const renderApp = (route = "/library") =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );

describe("MediaFlow frontend", () => {
  it("renders the Team A library workflow", () => {
    renderApp();

    expect(screen.getByRole("heading", { name: /your media library/i })).toBeInTheDocument();
    expect(screen.getByText("Customer story — Acme")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add media/i })).toBeInTheDocument();
  });

  it("filters the library by processing status", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(screen.getByRole("button", { name: /analyzing/i }));

    expect(screen.getByText("Founder notes — July")).toBeInTheDocument();
    expect(screen.queryByText("Customer story — Acme")).not.toBeInTheDocument();
  });

  it("opens a search result at its media asset", async () => {
    const user = userEvent.setup();
    renderApp("/search");

    await user.click(screen.getByRole("link", { name: /open at 00:31/i }));

    expect(screen.getByRole("heading", { name: "Customer story — Acme" })).toBeInTheDocument();
    expect(screen.getByText(/best parts of this video/i)).toBeInTheDocument();
  });
});
