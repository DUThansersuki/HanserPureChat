export const DEFAULT_CHAT_MODEL = "hanser/agent";

export const titleModel = {
  description: "Hanser Agent conversation title",
  gatewayOrder: [] as string[],
  id: DEFAULT_CHAT_MODEL,
  name: "Hanser Agent",
  provider: "hanser",
};

export type ModelCapabilities = {
  tools: boolean;
  vision: boolean;
  reasoning: boolean;
};

export type ChatModel = {
  id: string;
  name: string;
  provider: string;
  description: string;
  gatewayOrder?: string[];
  reasoningEffort?: "none" | "minimal" | "low" | "medium" | "high";
};

export const chatModels: ChatModel[] = [
  {
    description: "本地 Hanser Persona、Wiki 检索与长期记忆链路",
    id: DEFAULT_CHAT_MODEL,
    name: "Hanser Agent",
    provider: "hanser",
  },
];

const capabilities: Record<string, ModelCapabilities> = {
  [DEFAULT_CHAT_MODEL]: {
    reasoning: false,
    tools: false,
    vision: false,
  },
};

export async function getCapabilities() {
  return capabilities;
}

export const isDemo = false;

export type GatewayModelWithCapabilities = ChatModel & {
  capabilities: ModelCapabilities;
};

export async function getAllGatewayModels(): Promise<
  GatewayModelWithCapabilities[]
> {
  return chatModels.map((model) => ({
    ...model,
    capabilities: capabilities[model.id],
  }));
}

export function getActiveModels(): ChatModel[] {
  return chatModels;
}

export const allowedModelIds = new Set(chatModels.map((model) => model.id));

export const modelsByProvider = {
  hanser: chatModels,
};

export type ModelAvailability = "healthy" | "impacted" | "unknown";

export async function getModelAvailability(): Promise<ModelAvailability> {
  return "healthy";
}
