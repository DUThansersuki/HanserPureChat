import { z } from "zod";

const textPartSchema = z.object({
  text: z.string().min(1).max(12_000),
  type: z.literal("text"),
});

const messageSchema = z.object({
  id: z.string().min(1).max(128),
  parts: z.array(z.object({ type: z.string() }).passthrough()),
  role: z.enum(["user", "assistant", "system"]),
});

const newUserMessageSchema = messageSchema.extend({
  id: z.uuid(),
  parts: z.array(textPartSchema).min(1),
  role: z.literal("user"),
});

export const postRequestBodySchema = z.object({
  id: z.uuid(),
  message: newUserMessageSchema.optional(),
  messages: z.array(messageSchema).optional(),
  personaSettings: z
    .object({
      adult_innuendo_opt_in: z.boolean().default(false),
    })
    .optional(),
});

export type PostRequestBody = z.infer<typeof postRequestBodySchema>;
