import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import StatusBadge from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders the status label", () => {
    render(<StatusBadge status="picked" />);
    expect(screen.getByText("Picked")).toBeInTheDocument();
  });

  it("renders In Delivery status", () => {
    render(<StatusBadge status="in-delivery" />);
    expect(screen.getByText("In Delivery")).toBeInTheDocument();
  });

  it("renders Delivered status", () => {
    render(<StatusBadge status="delivered" />);
    expect(screen.getByText("Delivered")).toBeInTheDocument();
  });

  it("renders Issue status", () => {
    render(<StatusBadge status="issue" />);
    expect(screen.getByText("Issue")).toBeInTheDocument();
  });
});
