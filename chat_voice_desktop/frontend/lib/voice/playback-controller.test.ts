import assert from "node:assert/strict";
import test from "node:test";
import { PlaybackController } from "./playback-controller";
import type { PlaybackState } from "./contracts";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function observe(controller: PlaybackController) {
  const changes: Array<{ state: PlaybackState; replyId?: string }> = [];
  controller.subscribe((state, _position, _replayable, replyId) => changes.push({ state, replyId }));
  return changes;
}

const nextTick = () => new Promise<void>((resolve) => setImmediate(resolve));

test("replay identifies the reply while readiness is pending and can cancel before querying history", async (t) => {
  const controller = new PlaybackController();
  t.after(() => controller.dispose());
  const changes = observe(controller);
  const ready = deferred<void>();
  let requests = 0;
  t.mock.method(globalThis, "fetch", async () => { requests++; return Response.json({}); });
  let signal: AbortSignal | undefined;
  const replay = controller.replay("reply-a", (nextSignal) => { signal = nextSignal; return ready.promise; });
  assert.deepEqual(changes.at(-1), { state: "PREPARING", replyId: "reply-a" });
  await nextTick();
  await controller.interrupt();
  assert.equal(signal?.aborted, true);
  ready.resolve();
  await replay;
  assert.equal(requests, 0);
  assert.deepEqual(changes.at(-1), { state: "IDLE", replyId: undefined });
});

test("a repeated pending replay makes one history request and discards its late error after cancellation", async (t) => {
  const controller = new PlaybackController();
  t.after(() => controller.dispose());
  const changes = observe(controller);
  const history = deferred<Response>();
  let requests = 0;
  let signal: AbortSignal | undefined;
  t.mock.method(globalThis, "fetch", (_url: unknown, options: RequestInit) => {
    requests++;
    signal = options.signal ?? undefined;
    return history.promise;
  });
  const first = controller.replay("reply-a");
  await nextTick();
  await controller.replay("reply-a");
  assert.equal(requests, 1);
  await controller.interrupt();
  assert.equal(signal?.aborted, true);
  history.resolve(Response.json({ cause: "old history error" }, { status: 404 }));
  await assert.doesNotReject(first);
  assert.equal(changes.at(-1)?.state, "IDLE");
  assert.equal(changes.some((change) => change.state === "FAILED"), false);
});

test("a history link returned after cancellation cannot request segments", async (t) => {
  const controller = new PlaybackController();
  t.after(() => controller.dispose());
  const history = deferred<Response>();
  let requests = 0;
  t.mock.method(globalThis, "fetch", () => { requests++; return history.promise; });
  const replay = controller.replay("reply-a");
  await nextTick();
  await controller.interrupt();
  history.resolve(Response.json({ job_id: "old-job" }));
  await replay;
  assert.equal(requests, 1);
});

test("segments returned after cancellation cannot populate replay cache or start audio", async (t) => {
  const controller = new PlaybackController();
  t.after(() => controller.dispose());
  const job = deferred<Response>();
  let requests = 0;
  t.mock.method(globalThis, "fetch", () => ++requests === 1 ? Promise.resolve(Response.json({ job_id: "old-job" })) : job.promise);
  const replay = controller.replay("reply-a");
  await nextTick();
  assert.equal(requests, 2);
  await controller.interrupt();
  job.resolve(Response.json({ ready_segments: [{ index: 0, segment_id: "old-segment" }] }));
  await assert.doesNotReject(replay);
  assert.equal(controller.canReplay("reply-a"), false);
});

test("a superseded query cannot overwrite the new reply's preparing state", async (t) => {
  const controller = new PlaybackController();
  t.after(() => controller.dispose());
  const changes = observe(controller);
  const history = deferred<Response>();
  const ready = deferred<void>();
  t.mock.method(globalThis, "fetch", () => history.promise);
  const previous = controller.replay("reply-a");
  await nextTick();
  const current = controller.replay("reply-b", () => ready.promise);
  history.reject(new Error("late old query failure"));
  await assert.doesNotReject(previous);
  assert.deepEqual(changes.at(-1), { state: "PREPARING", replyId: "reply-b" });
  await controller.interrupt();
  ready.resolve();
  await current;
});

test("a current history failure is reported and sets the current reply to failed", async (t) => {
  const controller = new PlaybackController();
  t.after(() => controller.dispose());
  const changes = observe(controller);
  t.mock.method(globalThis, "fetch", async () => Response.json({ cause: "history missing" }, { status: 404 }));
  await assert.rejects(controller.replay("reply-a"), /history missing/);
  assert.deepEqual(changes.at(-1), { state: "FAILED", replyId: "reply-a" });
});

test("failed synthesis invalidates pending audio work and releases playback waiters", async (t) => {
  const controller = new PlaybackController();
  t.after(() => controller.dispose());
  const changes = observe(controller);
  // Exercise the same callback boundary used by SSE while an audio decode is pending.
  const internal = controller as unknown as {
    epoch: number; terminal: boolean; queue: unknown[]; replyId: string;
    aborter: AbortController; wake?: () => void; resumeWaiter?: () => void;
    sourceWaiter?: (result: "paused") => void;
    receiveEvent: (event: unknown, epoch: number) => void;
    pump: (epoch: number) => Promise<void>;
  };
  internal.replyId = "reply-a";
  internal.aborter = new AbortController();
  const oldEpoch = internal.epoch;
  internal.queue = [{}];
  let released = 0;
  internal.wake = internal.resumeWaiter = () => { released++; };
  internal.sourceWaiter = () => { released++; };
  internal.receiveEvent({ type: "turn.failed", reason: "runtime_restarted" }, oldEpoch);
  await internal.pump(oldEpoch);
  internal.receiveEvent({ type: "segment.ready", segment: { segment_id: "late" } }, oldEpoch);
  assert.equal(released, 3);
  assert.equal(internal.aborter.signal.aborted, true);
  assert.deepEqual(internal.queue, []);
  assert.equal(changes.at(-1)?.state, "FAILED");
  assert.equal(changes.some(({ state }) => ["READY", "PLAYING", "FINISHED"].includes(state)), false);
});

test("partial historical audio requires resynthesis and is not advertised as replayable", async (t) => {
  const controller = new PlaybackController();
  t.after(() => controller.dispose());
  let requests = 0;
  t.mock.method(globalThis, "fetch", async () => Response.json(++requests === 1 ? { job_id: "job-a" } : {
    status: "failed", partial: true, terminal_reason: "runtime_restarted",
    ready_segments: [{ segment_id: "segment-a", index: 0 }],
  }));
  await assert.rejects(controller.replay("reply-a"), /未完整合成.*runtime_restarted.*重新合成/);
  assert.equal(controller.canReplay("reply-a"), false);
});

test("a disconnected synthesis stream cancels its job and preserves failed state", async (t) => {
  const controller = new PlaybackController();
  t.after(() => controller.dispose());
  const changes = observe(controller);
  const previousContext = globalThis.AudioContext;
  globalThis.AudioContext = class { close() { return Promise.resolve(); } } as unknown as typeof AudioContext;
  t.after(() => { globalThis.AudioContext = previousContext; });
  const urls: string[] = [];
  t.mock.method(globalThis, "fetch", async (url: unknown) => {
    urls.push(String(url));
    if (String(url).endsWith("/events?after=0")) throw new Error("connection lost");
    return Response.json({ job_id: "job-a", status: "generating", ready_segments: [], last_sequence: 0 });
  });
  await assert.rejects(controller.start("reply-a"), /connection lost/);
  await nextTick();
  assert.equal(urls.some((url) => url.endsWith("/job-a/cancel")), true);
  assert.equal(changes.at(-1)?.state, "FAILED");
});
