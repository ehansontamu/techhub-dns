import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ErrorBoundary } from "./ErrorBoundary";

function ThrowingComponent() {
  throw new Error("test error");
}

function SafeComponent() {
  return <div>safe content</div>;
}

describe("ErrorBoundary", () => {
  it("renders children when there is no error", () => {
    render(
      <ErrorBoundary>
        <SafeComponent />
      </ErrorBoundary>
    );
    expect(screen.getByText("safe content")).toBeInTheDocument();
  });

  it("catches errors and renders fallback", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary fallback={({ error }) => <div data-testid="fallback">{error.message}</div>}>
        <ThrowingComponent />
      </ErrorBoundary>
    );
    spy.mockRestore();
    expect(screen.getByTestId("fallback")).toBeInTheDocument();
    expect(screen.getByText("test error")).toBeInTheDocument();
  });
});
