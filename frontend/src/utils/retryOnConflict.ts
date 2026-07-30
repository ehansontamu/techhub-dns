import { isAxiosError } from "axios";

export function isConflictError(error: unknown): boolean {
  return isAxiosError(error) && error.response?.status === 409;
}

export function getConflictActualUpdatedAt(error: unknown): string | null {
  if (!isConflictError(error) || !isAxiosError(error)) {
    return null;
  }

  const responseData = error.response?.data as {
    error?: {
      details?: {
        actual_updated_at?: unknown;
      };
    };
  } | undefined;
  const actualUpdatedAt = responseData?.error?.details?.actual_updated_at;
  return typeof actualUpdatedAt === "string" && actualUpdatedAt.trim()
    ? actualUpdatedAt
    : null;
}

export async function retryOnceOnConflict<TResult>({
  initialExpectedUpdatedAt,
  loadLatestExpectedUpdatedAt,
  attempt,
}: {
  initialExpectedUpdatedAt: string;
  loadLatestExpectedUpdatedAt: () => Promise<string>;
  attempt: (expectedUpdatedAt: string) => Promise<TResult>;
}): Promise<TResult> {
  try {
    return await attempt(initialExpectedUpdatedAt);
  } catch (error) {
    if (!isConflictError(error)) {
      throw error;
    }

    const actualUpdatedAt = getConflictActualUpdatedAt(error);
    if (actualUpdatedAt) {
      return attempt(actualUpdatedAt);
    }
  }

  const latestExpectedUpdatedAt = await loadLatestExpectedUpdatedAt();
  return attempt(latestExpectedUpdatedAt);
}
