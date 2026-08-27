import { AlertTriangle } from "lucide-react";

const DEVELOPMENT_HOSTNAME = "dev-techhub.pythonanywhere.com";

export function isDevelopmentHostname(hostname: string): boolean {
    return hostname.toLowerCase() === DEVELOPMENT_HOSTNAME;
}

export function DevelopmentBanner(): JSX.Element {
    return (
        <div
            role="status"
            className="fixed inset-x-0 top-0 z-[60] flex h-8 items-center justify-center gap-2 border-b border-amber-500 bg-amber-300 px-3 text-center text-xs font-medium text-amber-950 shadow-sm"
        >
            <AlertTriangle aria-hidden="true" className="h-4 w-4 flex-shrink-0" />
            <span>
                <span className="font-semibold">Development site</span>
                <span className="hidden sm:inline"> — you are not using production.</span>
            </span>
        </div>
    );
}
