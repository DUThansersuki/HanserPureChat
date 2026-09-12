import assert from "node:assert/strict";
import test from "node:test";
import type { SegmentPackage } from "./contracts";
import { enqueueUniqueSegment } from "./segment-queue";

function segment(segmentId: string, index: number) {
  return {
    index,
    segment_id: segmentId,
  } as SegmentPackage;
}

test("voice segments are ordered and accepted only once across reconnects", () => {
  const queue: SegmentPackage[] = [];
  const seen = new Set<string>();

  assert.equal(enqueueUniqueSegment(queue, seen, segment("second", 1)), true);
  assert.equal(enqueueUniqueSegment(queue, seen, segment("first", 0)), true);
  assert.deepEqual(
    queue.map((item) => item.segment_id),
    ["first", "second"]
  );

  assert.equal(queue.shift()?.segment_id, "first");
  assert.equal(enqueueUniqueSegment(queue, seen, segment("first", 0)), false);
  assert.deepEqual(
    queue.map((item) => item.segment_id),
    ["second"]
  );
});
