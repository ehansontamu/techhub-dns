import apiClient from "./client";

export type Vehicle = "van" | "golf_cart";

export type VehicleCheckoutType = "delivery_run" | "other";

export interface VehicleCheckoutResponse {
  id: string;
  vehicle: Vehicle;
  checked_out_by: string;
  checked_out_by_user_id?: string | null;
  checked_out_by_email?: string | null;
  checkout_type: VehicleCheckoutType;
  purpose?: string | null;
  checked_out_at: string;
  checked_in_at?: string | null;
}

export interface CheckoutRequest {
  vehicle: Vehicle;
  checkout_type?: VehicleCheckoutType;
  purpose?: string;
  notes?: string;
}

export interface CheckinRequest {
  vehicle: Vehicle;
  notes?: string;
}

export interface VehicleStatusItem {
  vehicle: Vehicle;
  checked_out: boolean;
  checked_out_by?: string | null;
  checked_out_by_user_id?: string | null;
  checkout_type?: VehicleCheckoutType | null;
  purpose?: string | null;
  checked_out_at?: string | null;
  delivery_run_active: boolean;
}

export interface VehicleStatusResponse {
  vehicles: VehicleStatusItem[];
}

export interface ListVehicleCheckoutsResponse {
  items: Array<{
    id: string;
    vehicle: Vehicle;
    checked_out_by: string;
    checked_out_by_user_id?: string | null;
    checked_out_by_email?: string | null;
    checkout_type: VehicleCheckoutType;
    purpose?: string | null;
    notes?: string | null;
    checked_out_at: string | null;
    checked_in_at: string | null;
    checked_in_by?: string | null;
  }>;
  page: number;
  page_size: number;
  total: number;
}

export const vehicleCheckoutsApi = {
  checkout: async (request: CheckoutRequest): Promise<VehicleCheckoutResponse> => {
    const response = await apiClient.post<VehicleCheckoutResponse>(
      "/vehicle-checkouts/checkout",
      request
    );
    return response.data;
  },

  checkin: async (request: CheckinRequest): Promise<VehicleCheckoutResponse> => {
    const response = await apiClient.post<VehicleCheckoutResponse>(
      "/vehicle-checkouts/checkin",
      request
    );
    return response.data;
  },

  getActive: async (): Promise<VehicleCheckoutResponse[]> => {
    const response = await apiClient.get<VehicleCheckoutResponse[]>(
      "/vehicle-checkouts/active"
    );
    return response.data;
  },

  getStatuses: async (): Promise<VehicleStatusResponse> => {
    const response = await apiClient.get<VehicleStatusResponse>(
      "/vehicles/status"
    );
    return response.data;
  },

  listCheckouts: async (query?: {
    vehicle?: Vehicle;
    checkout_type?: VehicleCheckoutType;
    start_date?: string;
    end_date?: string;
    page?: number;
    page_size?: number;
  }): Promise<ListVehicleCheckoutsResponse> => {
    const response = await apiClient.get<ListVehicleCheckoutsResponse>("/vehicle-checkouts", {
      params: query,
    });
    return response.data;
  },
};
