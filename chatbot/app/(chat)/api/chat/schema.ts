import { z } from "zod";

const textPartSchema = z.object({
  text: z.string().min(1).max(12_000),
  type: z.enum(["text"]),
});

const userMessageSchema = z.object({
  id: z.uuid(),
  parts: z.array(textPartSchema).min(1),
  role: z.enum(["user"]),
});

const toolApprovalMessageSchema = z.object({
  id: z.string(),
  parts: z.array(z.record(z.string(), z.unknown())),
  role: z.enum(["user", "assistant"]),
});

export const postRequestBodySchema = z.object({
  id: z.uuid(),
  message: userMessageSchema.optional(),
  messages: z.array(toolApprovalMessageSchema).optional(),
  outputPreferences: z
    .object({
      dynamic_live2d: z.boolean().default(false),
      offline_performance: z.boolean().default(false),
      speech: z.boolean().default(false),
    })
    .optional(),
  personaSettings: z
    .object({
      adult_innuendo_opt_in: z.boolean().default(false),
    })
    .optional(),
  selectedChatModel: z.string(),
  selectedVisibilityType: z.enum(["public", "private"]),
});

export type PostRequestBody = z.infer<typeof postRequestBodySchema>;
