import type { Live2DFrame } from "./timeline-evaluator";

export type RigCapabilities = {
  revision: string;
  mouthParameter?: string;
  expressionPresets: string[];
  motions: string[];
};

export interface RigAdapter {
  apply: (frame: Live2DFrame, epoch: number) => void;
  readonly capabilities: RigCapabilities;
  destroy: () => void;
  reset: (epoch: number) => void;
}

export class ParameterOwner {
  private activeEpoch = 0;

  claim(epoch: number) {
    if (epoch < this.activeEpoch) {
      return false;
    }
    this.activeEpoch = epoch;
    return true;
  }

  owns(epoch: number) {
    return epoch === this.activeEpoch;
  }
}
