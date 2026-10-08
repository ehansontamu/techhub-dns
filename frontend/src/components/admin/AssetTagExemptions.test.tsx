import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import AssetTagExemptions from "./AssetTagExemptions";

const tti = "TTI - Texas A&M Transportation Institute";

describe("AssetTagExemptions", () => {
    it("adds a complete College/Unit and removes the initial exemption", async () => {
        const user = userEvent.setup();
        const onSave = vi.fn().mockResolvedValue(undefined);
        render(<AssetTagExemptions value={JSON.stringify([tti])} saving={false} onSave={onSave} />);
        await act(async () => { await user.type(screen.getByLabelText("College/Unit to exempt"), "  Another   College  "); });
        await act(async () => { await user.click(screen.getByRole("button", { name: "Add College/Unit" })); });
        expect(onSave).toHaveBeenLastCalledWith(JSON.stringify([tti, "Another College"]));
        await act(async () => { await user.click(screen.getByRole("button", { name: `Remove ${tti}` })); });
        expect(onSave).toHaveBeenLastCalledWith("[]");
    });

    it("rejects duplicate units without saving", async () => {
        const user = userEvent.setup();
        const onSave = vi.fn();
        render(<AssetTagExemptions value={JSON.stringify([tti])} saving={false} onSave={onSave} />);
        await act(async () => { await user.type(screen.getByLabelText("College/Unit to exempt"), tti.toLowerCase()); });
        await act(async () => { await user.click(screen.getByRole("button", { name: "Add College/Unit" })); });
        expect(screen.getByRole("alert")).toHaveTextContent("already exempt");
        expect(onSave).not.toHaveBeenCalled();
    });

    it("keeps the draft and saved list when saving fails", async () => {
        const user = userEvent.setup();
        render(<AssetTagExemptions value={JSON.stringify([tti])} saving={false} onSave={vi.fn().mockRejectedValue(new Error("offline"))} />);
        await act(async () => { await user.type(screen.getByLabelText("College/Unit to exempt"), "Another College"); });
        await act(async () => { await user.click(screen.getByRole("button", { name: "Add College/Unit" })); });
        expect(screen.getByRole("alert")).toHaveTextContent("Unable to save");
        expect(screen.getByLabelText("College/Unit to exempt")).toHaveValue("Another College");
        expect(screen.getByText(tti)).toBeInTheDocument();
    });
});
