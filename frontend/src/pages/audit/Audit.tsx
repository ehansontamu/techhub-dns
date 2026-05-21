import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Order, OrderStatus } from "../../types/order";
import { getOrdersListQueryOptions } from "../../queries/orders";
import { formatToCentralTime } from "../../utils/timezone";
import { formatDeliveryLocation } from "../../utils/location";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Skeleton } from "../../components/Skeleton";
import { Calendar, MapPin, User, Clock, PackageSearch } from "lucide-react";

interface AuditFilters {
  startDate: string;
  endDate: string;
  location: string;
  deliverer: string;
}

export default function Audit() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState<AuditFilters>({
    startDate: "",
    endDate: "",
    location: "",
    deliverer: "",
  });

  const ordersQuery = useQuery(
    getOrdersListQueryOptions({
      status: OrderStatus.DELIVERED,
      search: "",
    })
  );

  const orders: Order[] = ordersQuery.data?.items ?? [];
  const isLoading = ordersQuery.isPending;

  const filteredOrders = useMemo(() => {
    return orders.filter((order) => {
      // Date range filter (using updated_at as delivered timestamp)
      if (filters.startDate) {
        const orderDate = new Date(order.updated_at);
        const startDate = new Date(filters.startDate);
        startDate.setHours(0, 0, 0, 0);
        if (orderDate < startDate) return false;
      }
      if (filters.endDate) {
        const orderDate = new Date(order.updated_at);
        const endDate = new Date(filters.endDate);
        endDate.setHours(23, 59, 59, 999);
        if (orderDate > endDate) return false;
      }

      // Location text filter
      if (filters.location.trim()) {
        const location = formatDeliveryLocation(order).toLowerCase();
        if (!location.includes(filters.location.trim().toLowerCase())) {
          return false;
        }
      }

      // Deliverer text filter
      if (filters.deliverer.trim()) {
        const deliverer = (order.assigned_deliverer || "").toLowerCase();
        if (!deliverer.includes(filters.deliverer.trim().toLowerCase())) {
          return false;
        }
      }

      return true;
    });
  }, [orders, filters]);

  const handleFilterChange = (key: keyof AuditFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
  };

  const clearFilters = () => {
    setFilters({
      startDate: "",
      endDate: "",
      location: "",
      deliverer: "",
    });
  };

  const sortedOrders = useMemo(() => {
    return [...filteredOrders].sort(
      (a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    );
  }, [filteredOrders]);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Filters */}
      <div className="flex flex-col gap-4 rounded-lg border p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Filters</h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={clearFilters}
            className="text-xs"
          >
            Clear all
          </Button>
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-2">
            <label
              htmlFor="start-date"
              className="text-sm font-medium flex items-center gap-2"
            >
              <Calendar className="h-4 w-4" />
              Start Date
            </label>
            <Input
              id="start-date"
              type="date"
              value={filters.startDate}
              onChange={(e) => handleFilterChange("startDate", e.target.value)}
              placeholder="Filter by start date"
            />
          </div>
          <div className="space-y-2">
            <label
              htmlFor="end-date"
              className="text-sm font-medium flex items-center gap-2"
            >
              <Calendar className="h-4 w-4" />
              End Date
            </label>
            <Input
              id="end-date"
              type="date"
              value={filters.endDate}
              onChange={(e) => handleFilterChange("endDate", e.target.value)}
              placeholder="Filter by end date"
            />
          </div>
          <div className="space-y-2">
            <label
              htmlFor="location-filter"
              className="text-sm font-medium flex items-center gap-2"
            >
              <MapPin className="h-4 w-4" />
              Location
            </label>
            <Input
              id="location-filter"
              type="text"
              value={filters.location}
              onChange={(e) => handleFilterChange("location", e.target.value)}
              placeholder="Filter by location"
            />
          </div>
          <div className="space-y-2">
            <label
              htmlFor="deliverer-filter"
              className="text-sm font-medium flex items-center gap-2"
            >
              <User className="h-4 w-4" />
              Deliverer
            </label>
            <Input
              id="deliverer-filter"
              type="text"
              value={filters.deliverer}
              onChange={(e) => handleFilterChange("deliverer", e.target.value)}
              placeholder="Filter by deliverer"
            />
          </div>
        </div>
      </div>

      {/* Orders Table */}
      {sortedOrders.length === 0 ? (
        <div className="py-12 text-center text-muted-foreground">
          <PackageSearch className="mx-auto mb-3 h-8 w-8 text-muted-foreground/40" />
          <p className="text-sm font-medium">No delivered orders found</p>
          <p className="text-xs text-muted-foreground/80">
            {isLoading
              ? "Loading..."
              : "Try adjusting your filters or check back later."}
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-border/70 bg-card/80 shadow-none">
          <div className="overflow-x-auto">
            <Table className="min-w-[800px]">
              <TableHeader className="sticky top-0 z-20 bg-muted/40">
                <TableRow>
                  <TableHead className="w-[140px]">Order ID</TableHead>
                  <TableHead>Location</TableHead>
                  <TableHead>Deliverer</TableHead>
                  <TableHead>Delivered On</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedOrders.map((order) => (
                  <TableRow
                    key={order.id}
                    className="hover:bg-muted/30 cursor-pointer transition-colors"
                    onClick={() => navigate(`/orders/${order.id}`)}
                  >
                    <TableCell className="font-medium">
                      {order.inflow_order_id || order.id}
                    </TableCell>
                    <TableCell>{formatDeliveryLocation(order)}</TableCell>
                    <TableCell>{order.assigned_deliverer || "—"}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      <span className="flex items-center gap-2">
                        <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                        {formatToCentralTime(order.updated_at, "MMM d, yyyy HH:mm")}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-800">
                        Delivered
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  );
}
