import { apiClient } from "./client";

export const COMPATIBILITY_EDITOR_DETAIL_FIELDS = [
  "display",
  "charging",
  "usbDetection",
  "ethernet",
  "audio",
  "sdCard",
] as const;

export const COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES = [
  "Functional",
  "Partially Functional",
  "Non-functional",
  "N/A",
] as const;

export const COMPATIBILITY_EDITOR_STATUS_VALUES = [
  "Compatible",
  "Incompatible",
  "Partially Compatible",
] as const;

export type CompatibilityEditorDetailField = (typeof COMPATIBILITY_EDITOR_DETAIL_FIELDS)[number];
export type CompatibilityEditorDetailStatus = (typeof COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES)[number];
export type CompatibilityEditorStatus = (typeof COMPATIBILITY_EDITOR_STATUS_VALUES)[number];

export const COMPATIBILITY_EDITOR_DETAIL_LABELS: Record<CompatibilityEditorDetailField, string> = {
  display: "Display(s)",
  charging: "Charging",
  usbDetection: "Thumb drive detection",
  ethernet: "Ethernet connection",
  audio: "3.5mm audio",
  sdCard: "SD card slot",
};

export interface CompatibilityEditorDock {
  name: string;
  url?: string;
  hidden?: boolean;
  studentEdited?: boolean;
  [key: string]: unknown;
}

export interface CompatibilityEditorCell {
  compatibilityStatus?: CompatibilityEditorStatus;
  display?: CompatibilityEditorDetailStatus;
  charging?: CompatibilityEditorDetailStatus;
  usbDetection?: CompatibilityEditorDetailStatus;
  ethernet?: CompatibilityEditorDetailStatus;
  audio?: CompatibilityEditorDetailStatus;
  sdCard?: CompatibilityEditorDetailStatus;
  rebootNeeded?: boolean | string;
  notes?: string;
  studentEdited?: boolean;
  [key: string]: unknown;
}

export interface CompatibilityEditorComputer {
  name: string;
  url?: string;
  hidden?: boolean;
  studentEdited?: boolean;
  incompatibleWith?: string[];
  partiallyCompatibleWith?: string[];
  compatibilityNotes?: Record<string, string>;
  compatibilityData?: Record<string, CompatibilityEditorCell>;
  [key: string]: unknown;
}

export interface CompatibilityEditorPayload {
  computers: Record<string, CompatibilityEditorComputer>;
  docks: Record<string, CompatibilityEditorDock>;
  [key: string]: unknown;
}

export const compatibilityEditorApi = {
  async getStagingData(): Promise<CompatibilityEditorPayload> {
    const response = await apiClient.get("/system/compatibility-editor-staging");
    return response.data;
  },

  async saveStagingData(payload: CompatibilityEditorPayload): Promise<{ success: boolean }> {
    const response = await apiClient.put("/system/compatibility-editor-staging", payload);
    return response.data;
  },
};
