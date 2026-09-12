export const HANSER_SCENE_EVENT = "hanser:scene";

export type HanserSceneName = "sing-it-is-like-a-star";

export type HanserSceneEventDetail = {
  action: "start" | "stop";
  scene: HanserSceneName;
};

export function triggerHanserScene(scene: HanserSceneName) {
  window.dispatchEvent(
    new CustomEvent<HanserSceneEventDetail>(HANSER_SCENE_EVENT, {
      detail: { action: "start", scene },
    })
  );
}

export function stopHanserScene(scene: HanserSceneName) {
  window.dispatchEvent(
    new CustomEvent<HanserSceneEventDetail>(HANSER_SCENE_EVENT, {
      detail: { action: "stop", scene },
    })
  );
}
