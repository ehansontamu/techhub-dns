import { queryOptions } from "@tanstack/react-query";

import { deliveryRunsApi, type DeliveryRunDetailResponse, type DeliveryRunResponse } from "../api/deliveryRuns";

export type ActiveDeliveryRun = DeliveryRunResponse & { order_ids: string[] };

export const deliveryRunsQueryKeys = {
  all: ["delivery-runs"] as const,
  active: () => [...deliveryRunsQueryKeys.all, "active"] as const,
  history: (params: { status: string[]; start_date?: string; end_date?: string }) =>
    [...deliveryRunsQueryKeys.all, "history", params] as const,
  details: () => [...deliveryRunsQueryKeys.all, "detail"] as const,
  detail: (runId: string) => [...deliveryRunsQueryKeys.details(), runId] as const,
};

export const getActiveDeliveryRunsQueryOptions = () =>
  queryOptions({
    queryKey: deliveryRunsQueryKeys.active(),
    queryFn: (): Promise<ActiveDeliveryRun[]> => deliveryRunsApi.getActiveRuns(),
  });

export const getDeliveryRunDetailQueryOptions = (runId: string) =>
  queryOptions({
    queryKey: deliveryRunsQueryKeys.detail(runId),
    queryFn: (): Promise<DeliveryRunDetailResponse> => deliveryRunsApi.getRun(runId),
  });

export const getDeliveryRunHistoryQueryOptions = (params: {
  status: string[];
  start_date?: string;
  end_date?: string;
}) =>
  queryOptions({
    queryKey: deliveryRunsQueryKeys.history(params),
    queryFn: (): Promise<DeliveryRunResponse[]> =>
      deliveryRunsApi.getRuns({
        status: params.status,
        start_date: params.start_date,
        end_date: params.end_date,
      }),
    staleTime: 60_000,
  });
