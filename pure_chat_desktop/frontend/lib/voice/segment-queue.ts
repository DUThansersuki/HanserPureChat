import type { SegmentPackage } from "./contracts";

export function enqueueUniqueSegment(
  queue: SegmentPackage[],
  seenSegmentIds: Set<string>,
  segment: SegmentPackage
) {
  if (seenSegmentIds.has(segment.segment_id)) {
    return false;
  }
  seenSegmentIds.add(segment.segment_id);
  queue.push(segment);
  queue.sort((a, b) => a.index - b.index);
  return true;
}
