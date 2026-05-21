import { QueryErrorResetBoundary } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { reportFrontendError } from "../../lib/errorReporting";
import { ErrorBoundary } from "./ErrorBoundary";
import { ErrorFallback } from "./ErrorFallback";

interface BoundaryProps {
  children: ReactNode;
}

interface SectionBoundaryProps extends BoundaryProps {
  title: string;
  message: string;
  resetKeys?: unknown[];
}

export function AppShellErrorBoundary({ children }: BoundaryProps) {
  const location = useLocation();

  return (
    <QueryErrorResetBoundary>
      {({ reset }) => (
        <ErrorBoundary
          onError={(error, errorInfo) => {
            void reportFrontendError(error, errorInfo, {
              boundary: "app-shell",
              pathname: location.pathname,
            });
          }}
          onReset={reset}
          resetKeys={[location.pathname]}
          fallback={({ error, reset: resetBoundary }) => (
            <ErrorFallback
              error={error}
              fullScreen
              title="The app hit an unexpected error"
              message="Try reloading the app. If the problem keeps happening, navigate to another page or refresh the browser session."
              onRetry={resetBoundary}
              onReload={() => window.location.reload()}
            />
          )}
        >
          {children}
        </ErrorBoundary>
      )}
    </QueryErrorResetBoundary>
  );
}

export function RouteContentErrorBoundary({ children }: BoundaryProps) {
  const location = useLocation();

  return (
    <QueryErrorResetBoundary>
      {({ reset }) => (
        <ErrorBoundary
          onError={(error, errorInfo) => {
            void reportFrontendError(error, errorInfo, {
              boundary: "route-content",
              pathname: location.pathname,
            });
          }}
          onReset={reset}
          resetKeys={[location.pathname]}
          fallback={({ error, reset: resetBoundary }) => (
            <ErrorFallback
              error={error}
              title="This page could not finish rendering"
              message="Please refresh the page."
              onRetry={resetBoundary}
            />
          )}
        >
          {children}
        </ErrorBoundary>
      )}
    </QueryErrorResetBoundary>
  );
}

export function SectionErrorBoundary({ children, title, message, resetKeys = [] }: SectionBoundaryProps) {
  const location = useLocation();

  return (
    <QueryErrorResetBoundary>
      {({ reset }) => (
        <ErrorBoundary
          onError={(error, errorInfo) => {
            void reportFrontendError(error, errorInfo, {
              boundary: "section",
              title,
              pathname: location.pathname,
            });
          }}
          onReset={reset}
          resetKeys={[location.pathname, ...resetKeys]}
          fallback={({ error, reset: resetBoundary }) => (
            <ErrorFallback
              error={error}
              title={title}
              message={message}
              onRetry={resetBoundary}
            />
          )}
        >
          {children}
        </ErrorBoundary>
      )}
    </QueryErrorResetBoundary>
  );
}
