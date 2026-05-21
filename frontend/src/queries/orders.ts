import type { QueryClient } from "@tanstack/react-query";
import { queryOptions } from "@tanstack/react-query";

import { ordersApi } from "../api/orders";
import type { AuditLog, Order, OrderDetail, OrderStatus } from "../types/order";

export type OrdersListStatusFilter = OrderStatus | OrderStatus[] | null;

export interface OrdersListFilters {
  status: OrdersListStatusFilter;
  search: string;
  skip?: number;
  limit?: number;
}

export interface TagRequestCandidatesFilters {
  limit?: number;
  search?: string;
}

const compareOrderListPriority = (left: Order, right: Order): number => {
  const updatedAtDelta = Date.parse(right.updated_at) - Date.parse(left.updated_at);
  if (!Number.isNaN(updatedAtDelta) && updatedAtDelta !== 0) {
    return updatedAtDelta;
  }

  const createdAtDelta = Date.parse(right.created_at) - Date.parse(left.created_at);
  if (!Number.isNaN(createdAtDelta) && createdAtDelta !== 0) {
    return createdAtDelta;
  }

  const leftKey = left.inflow_order_id || left.id || "";
  const rightKey = right.inflow_order_id || right.id || "";
  return rightKey.localeCompare(leftKey);
};

const normalizeStatuses = (status: OrdersListStatusFilter): OrderStatus[] => {
  if (!status) {
    return [];
  }

  if (Array.isArray(status)) {
    return [...status];
  }

  return [status];
};

const normalizeSearch = (search: string): string => search.trim();

export const ordersQueryKeys = {
  all: ["orders"] as const,
  lists: () => [...ordersQueryKeys.all, "list"] as const,
  list: (filters: OrdersListFilters) =>
    [
      ...ordersQueryKeys.lists(),
      {
        status: normalizeStatuses(filters.status),
        search: normalizeSearch(filters.search),
        skip: filters.skip ?? 0,
        limit: filters.limit ?? 100,
      },
    ] as const,
  details: () => [...ordersQueryKeys.all, "detail"] as const,
  detail: (orderId: string) => [...ordersQueryKeys.details(), orderId] as const,
  audits: () => [...ordersQueryKeys.all, "audit"] as const,
  audit: (orderId: string) => [...ordersQueryKeys.audits(), orderId] as const,
  tagRequestCandidates: (filters: TagRequestCandidatesFilters = {}) =>
    [...ordersQueryKeys.all, "tag-request-candidates", { limit: filters.limit ?? null, search: filters.search?.trim() ?? "" }] as const,
};

const fetchOrdersList = async (filters: OrdersListFilters): Promise<{ items: Order[]; total: number }> => {
  const normalizedStatuses = normalizeStatuses(filters.status);
  const search = normalizeSearch(filters.search) || undefined;

  if (normalizedStatuses.length > 1) {
    const results = await Promise.all(
      normalizedStatuses.map((status) =>
        ordersApi.getOrders({
          status,
          search,
          skip: filters.skip,
          limit: filters.limit,
        })
      )
    );

    return {
      items: results.flatMap((r) => r.items).sort(compareOrderListPriority),
      total: Math.max(...results.map((r) => r.total), 0),
    };
  }

  return ordersApi.getOrders({
    status: normalizedStatuses[0],
    search,
    skip: filters.skip,
    limit: filters.limit,
  }).then((r) => ({ items: r.items, total: r.total }));
};

export const getOrdersListQueryOptions = (filters: OrdersListFilters) =>
  queryOptions({
    queryKey: ordersQueryKeys.list(filters),
    queryFn: () => fetchOrdersList(filters),
  });

export const getOrderDetailQueryOptions = (orderId: string) =>
  queryOptions({
    queryKey: ordersQueryKeys.detail(orderId),
    queryFn: (): Promise<OrderDetail> => ordersApi.getOrder(orderId),
  });

export const getOrderAuditQueryOptions = (orderId: string) =>
  queryOptions({
    queryKey: ordersQueryKeys.audit(orderId),
    queryFn: (): Promise<AuditLog[]> => ordersApi.getOrderAudit(orderId),
  });

export const getTagRequestCandidatesQueryOptions = (filters: TagRequestCandidatesFilters = {}) =>
  queryOptions({
    queryKey: ordersQueryKeys.tagRequestCandidates(filters),
    queryFn: (): Promise<Order[]> =>
      ordersApi.getTagRequestCandidates({
        limit: filters.limit,
        search: filters.search?.trim() || undefined,
      }),
  });

export const invalidateOrderQueries = async (queryClient: QueryClient, orderId?: string): Promise<void> => {
  await queryClient.invalidateQueries({ queryKey: ordersQueryKeys.lists() });

  if (!orderId) {
    return;
  }

  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ordersQueryKeys.detail(orderId) }),
    queryClient.invalidateQueries({ queryKey: ordersQueryKeys.audit(orderId) }),
  ]);
};
