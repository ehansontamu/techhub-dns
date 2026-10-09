import { useState } from "react";

import { Button } from "../ui/button";
import { Input } from "../ui/input";

interface AssetTagExemptionsProps {
    value: string;
    saving: boolean;
    onSave: (value: string) => Promise<void>;
}

export default function AssetTagExemptions({ value, saving, onSave }: AssetTagExemptionsProps) {
    const [draft, setDraft] = useState("");
    const [error, setError] = useState<string | null>(null);
    let units: string[];
    try {
        const parsed: unknown = JSON.parse(value);
        if (!Array.isArray(parsed) || !parsed.every((item): item is string => typeof item === "string")) {
            throw new Error("Invalid College/Unit list");
        }
        units = parsed;
    } catch {
        return <p role="alert">Unable to read the College/Unit exemptions. Reload this panel.</p>;
    }

    const addUnit = async () => {
        const unit = draft.trim().replace(/\s+/g, " ");
        if (!unit) return;
        if (units.some((existing) => existing.toLowerCase() === unit.toLowerCase())) {
            setError("This College/Unit is already exempt.");
            return;
        }
        setError(null);
        try {
            await onSave(JSON.stringify([...units, unit]));
            setDraft("");
        } catch {
            setError("Unable to save exemptions. Please try again.");
        }
    };

    const removeUnit = async (unit: string) => {
        setError(null);
        try {
            await onSave(JSON.stringify(units.filter((existing) => existing !== unit)));
        } catch {
            setError("Unable to save exemptions. Please try again.");
        }
    };

    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
                Orders for these College/Units are excluded from tag requests and can proceed without tagging.
                Match the complete College/Unit value from inFlow; capitalization and extra spaces are ignored.
                Removing a unit restores the usual tagging rules. Existing tag requests are not cancelled.
            </p>
            <ul className="space-y-2">
                {units.map((unit) => (
                    <li key={unit} className="flex items-center justify-between gap-3 rounded-lg border border-border/70 p-3">
                        <span className="text-sm">{unit}</span>
                        <Button variant="outline" size="sm" disabled={saving} onClick={() => void removeUnit(unit)} aria-label={`Remove ${unit}`}>
                            Remove
                        </Button>
                    </li>
                ))}
            </ul>
            {units.length === 0 && <p className="text-sm text-muted-foreground">No College/Units are exempt.</p>}
            <form
                onSubmit={(event) => { event.preventDefault(); void addUnit(); }}
                className="flex flex-wrap gap-2"
            >
                <Input
                    aria-label="College/Unit to exempt"
                    placeholder="Enter the complete College/Unit"
                    value={draft}
                    maxLength={500}
                    disabled={saving}
                    onChange={(event) => { setDraft(event.target.value); setError(null); }}
                    className="min-w-60 flex-1"
                />
                <Button type="submit" disabled={saving || !draft.trim()}>{saving ? "Saving..." : "Add College/Unit"}</Button>
            </form>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        </div>
    );
}
