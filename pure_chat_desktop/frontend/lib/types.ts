import type { UIMessage } from "ai";
import { z } from "zod";

export const messageMetadataSchema = z.object({
  createdAt: z.string(),
});

export type MessageMetadata = z.infer<typeof messageMetadataSchema>;

export type WaitingStatusData = {
  phase: "waiting" | "still-waiting" | "thinking";
  message: string;
  modelId: string;
  modelName: string;
};
export type HanserMetaData = {
  degradedReasons: string[];
  postTurnRetryId?: string;
  postTurnStatus: "completed" | "pending_retry";
  replyId?: string;
  requestId?: string;
  sourceCount: number;
  status: "ok" | "degraded";
  traceId?: string;
};

export type CustomUIDataTypes = {
  "waiting-status": WaitingStatusData;
  "hanser-meta": HanserMetaData;
};

export type ChatMessage = UIMessage<MessageMetadata, CustomUIDataTypes>;

export type ChatOverview = {
  createdAt: string;
  id: string;
  title: string;
  userId: string;
};
