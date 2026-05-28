import { apiClient } from "./client";

export type BigCommerceChatRole = "user" | "assistant";

export interface BigCommerceChatMessage {
  role: BigCommerceChatRole;
  content: string;
}

export interface BigCommerceChatResponse {
  answer: string;
  messages: BigCommerceChatMessage[];
}

export const bigcommerceChatApi = {
  async ask(
    question: string,
    messages: BigCommerceChatMessage[]
  ): Promise<BigCommerceChatResponse> {
    const response = await apiClient.post("/bigcommerce-chat", {
      question,
      messages,
    });
    return response.data;
  },
};
