import { isAxiosError } from "axios";

export function isConflictError(error: unknown): boolean {
  return isAxiosError(error) && error.response?.status === 409;
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
  }

  const latestExpectedUpdatedAt = await loadLatestExpectedUpdatedAt();
  return attempt(latestExpectedUpdatedAt);
}
