export type PlaybackState =
  | "IDLE"
  | "PREPARING"
  | "WAITING_USER_GESTURE"
  | "READY"
  | "PLAYING"
  | "PAUSED"
  | "BUFFERING"
  | "FINISHED"
  | "INTERRUPTED"
  | "FAILED";

export type AudioArtifact = {
  artifact_id: string;
  sample_rate: number;
  channels: 1;
  sample_count: number;
};

export type SegmentPackage = {
  schema_version: "1.1";
  job_id: string;
  reply_id: string;
  rendition_id: string;
  segment_id: string;
  index: number;
  audio: AudioArtifact;
  segment_duration_samples: number;
  timeline: {
    artifact_id: string;
    revision: string;
    clock: "audio";
    sample_rate: number;
    pause_after_samples: number;
    mouth_precision: "amplitude" | "rhubarb" | "closed";
    mouth: Array<{
      start_sample: number;
      end_sample: number;
      value: number;
    }>;
    expression: {
      preset?: string;
      weight?: number;
      motion?: string;
      attack_ms?: number;
      release_ms?: number;
    };
  };
};

export type VoiceJob = {
  schema_version: "1.1";
  job_id: string;
  reply_id: string;
  rendition_id: string;
  status:
    | "queued"
    | "preparing"
    | "generating"
    | "completed"
    | "failed"
    | "cancelled";
  last_sequence: number;
  ready_segments: SegmentPackage[];
  terminal_reason?: string;
};

export type VoiceJobEvent = {
  schema_version: "1.1";
  job_id: string;
  reply_id: string;
  rendition_id: string;
  sequence: number;
  type:
    | "turn.started"
    | "segment.ready"
    | "turn.completed"
    | "turn.failed"
    | "turn.cancelled";
  segment?: SegmentPackage;
  reason?: string;
};

export type PlaybackPosition = {
  epoch: number;
  segmentId: string;
  phase: "audio" | "gap";
  sampleOffset: number;
  timelineRevision: string;
};

export type PlaybackSample = {
  position: PlaybackPosition;
  segment: SegmentPackage;
};
