import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DevelopmentBanner, isDevelopmentHostname } from "./DevelopmentBanner";

describe("DevelopmentBanner", () => {
    it("identifies only the development deployment hostname", () => {
        expect(isDevelopmentHostname("dev-techhub.pythonanywhere.com")).toBe(true);
        expect(isDevelopmentHostname("DEV-TECHHUB.PYTHONANYWHERE.COM")).toBe(true);
        expect(isDevelopmentHostname("techhub.pythonanywhere.com")).toBe(false);
        expect(isDevelopmentHostname("localhost")).toBe(false);
    });

    it("renders a persistent development warning", () => {
        render(<DevelopmentBanner />);

        expect(screen.getByRole("status")).toHaveTextContent(
            "Development site — you are not using production."
        );
    });
});
