"use client";

import { useEffect, useRef, useState } from "react";
import { useVoice } from "@/components/voice/voice-provider";
import type { MmdRigAdapter } from "@/lib/live2d/mmd-rig-adapter";
import {
  HANSER_SCENE_EVENT,
  type HanserSceneEventDetail,
} from "@/lib/live2d/scene-events";
import { evaluateAudioFrame } from "@/lib/live2d/timeline-evaluator";
import songMouthTimeline from "@/public/media/9-it-is-like-a-star.mouth.json";

export type StageState = "error" | "loading" | "ready";

export function live2dModelUrl(basePath: string, assetName: string) {
  return `${basePath}/api/live2d/model/${assetName}`;
}

function sampleSongMouth(elapsedSeconds: number) {
  const frame = elapsedSeconds * songMouthTimeline.framesPerSecond;
  const firstIndex = Math.min(
    songMouthTimeline.values.length - 1,
    Math.max(0, Math.floor(frame))
  );
  const secondIndex = Math.min(
    songMouthTimeline.values.length - 1,
    firstIndex + 1
  );
  const progress = frame - firstIndex;
  const first = songMouthTimeline.values[firstIndex] ?? 0;
  const second = songMouthTimeline.values[secondIndex] ?? first;
  return first + (second - first) * progress;
}

export function MmdStage({
  onStateChange,
}: {
  onStateChange: (state: StageState) => void;
}) {
  const {
    interrupt,
    samplePerformance,
    state: voiceState,
    volume,
  } = useVoice();
  const interruptRef = useRef(interrupt);
  const samplePerformanceRef = useRef(samplePerformance);
  const voiceStateRef = useRef(voiceState);
  const lastVoiceEpochRef = useRef(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const adapterRef = useRef<MmdRigAdapter | null>(null);
  const songAudioRef = useRef<HTMLAudioElement | null>(null);
  const [stageEnabled, setStageEnabled] = useState(false);
  const [isSinging, setIsSinging] = useState(false);

  interruptRef.current = interrupt;
  samplePerformanceRef.current = samplePerformance;
  voiceStateRef.current = voiceState;

  useEffect(() => {
    const mediaQuery = window.matchMedia("(min-width: 1024px)");
    const syncStage = () => setStageEnabled(mediaQuery.matches);
    syncStage();
    mediaQuery.addEventListener("change", syncStage);
    return () => mediaQuery.removeEventListener("change", syncStage);
  }, []);

  useEffect(() => {
    const audio = new Audio(
      `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/media/9-it-is-like-a-star.mp3`
    );
    audio.preload = "auto";
    songAudioRef.current = audio;
    const handlePlay = () => setIsSinging(true);
    const handleStop = () => setIsSinging(false);
    audio.addEventListener("play", handlePlay);
    audio.addEventListener("pause", handleStop);
    audio.addEventListener("ended", handleStop);

    const handleScene = (event: Event) => {
      const { detail } = event as CustomEvent<HanserSceneEventDetail>;
      if (detail.scene !== "sing-it-is-like-a-star") {
        return;
      }
      if (detail.action === "stop") {
        audio.pause();
        audio.currentTime = 0;
        adapterRef.current?.setSceneFrame(undefined);
        return;
      }
      interruptRef.current().catch(() => undefined);
      audio.currentTime = 0;
      audio.play().catch((error: unknown) => {
        console.error("Song scene failed to start", error);
      });
    };

    window.addEventListener(HANSER_SCENE_EVENT, handleScene);
    return () => {
      window.removeEventListener(HANSER_SCENE_EVENT, handleScene);
      audio.removeEventListener("play", handlePlay);
      audio.removeEventListener("pause", handleStop);
      audio.removeEventListener("ended", handleStop);
      audio.pause();
      songAudioRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (songAudioRef.current) {
      songAudioRef.current.volume = volume;
    }
  }, [volume]);

  useEffect(() => {
    if (!stageEnabled) {
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    let disposed = false;
    let animationFrame = 0;
    let renderer: import("three").WebGLRenderer | undefined;
    let mesh: import("three").SkinnedMesh | undefined;

    async function start() {
      onStateChange("loading");
      const THREE = await import("three");
      const [{ MMDLoader }, { MmdRigAdapter: Adapter }] = await Promise.all([
        import("three/addons/loaders/MMDLoader.js"),
        import("@/lib/live2d/mmd-rig-adapter"),
      ]);
      if (disposed || !canvas) {
        return;
      }

      const manager = new THREE.LoadingManager();
      const allAssetsLoaded = new Promise<void>((resolve) => {
        manager.onLoad = resolve;
      });
      manager.setURLModifier((url) =>
        url
          .replace(/%5C/gi, "/")
          .replace(/\\/g, "/")
          .replace(/\/TEX\//i, "/tex/")
      );

      const loader = new MMDLoader(manager);
      mesh = await new Promise<import("three").SkinnedMesh>(
        (resolve, reject) => {
          loader.load(
            live2dModelUrl(
              process.env.NEXT_PUBLIC_BASE_PATH ?? "",
              "hanser_ver2.0.pmx"
            ),
            resolve,
            undefined,
            reject
          );
        }
      );
      await allAssetsLoaded;
      if (disposed || !mesh) {
        return;
      }

      renderer = new THREE.WebGLRenderer({
        alpha: true,
        antialias: true,
        canvas,
        powerPreference: "high-performance",
      });
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.2;
      renderer.setClearColor(0x00_00_00, 0);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

      const materials = Array.isArray(mesh.material)
        ? mesh.material
        : [mesh.material];
      const wristDecorationMaterials = materials.filter(
        (material) => material.name === "右腕装饰" || material.name === "手环"
      );
      const maxAnisotropy = renderer.capabilities.getMaxAnisotropy();
      for (const material of materials) {
        const toonMaterial = material as import("three").Material & {
          emissive?: import("three").Color;
          emissiveIntensity?: number;
          gradientMap?: import("three").Texture;
          map?: import("three").Texture;
          userData: {
            MMD?: { mapFileName?: string };
          };
        };
        if (toonMaterial.emissive && toonMaterial.map) {
          toonMaterial.emissive.set(0x00_00_00);
        }
        if (toonMaterial.map) {
          toonMaterial.map.anisotropy = Math.min(maxAnisotropy, 8);
          toonMaterial.map.colorSpace = THREE.SRGBColorSpace;
          toonMaterial.map.needsUpdate = true;
        }
        const mapFileName = toonMaterial.userData.MMD?.mapFileName
          ?.replaceAll("\\", "/")
          .toLowerCase();
        if (mapFileName?.includes("hair")) {
          toonMaterial.emissive?.setRGB(0.022, 0.007, 0.003);
          toonMaterial.emissiveIntensity = 0.7;
        } else if (
          mapFileName?.includes("face") ||
          mapFileName?.includes("body")
        ) {
          toonMaterial.emissive?.setRGB(0.018, 0.006, 0.004);
          toonMaterial.emissiveIntensity = 0.55;
        } else if (mapFileName?.includes("eyes")) {
          toonMaterial.emissive?.setRGB(0.025, 0.018, 0.004);
          toonMaterial.emissiveIntensity = 0.8;
        }
        if (toonMaterial.gradientMap) {
          toonMaterial.gradientMap.magFilter = THREE.LinearFilter;
          toonMaterial.gradientMap.minFilter = THREE.LinearFilter;
          toonMaterial.gradientMap.needsUpdate = true;
        }
        material.needsUpdate = true;
      }

      const scene = new THREE.Scene();
      const camera = new THREE.OrthographicCamera(-5, 5, 8, -8, 0.1, 200);

      scene.add(new THREE.HemisphereLight(0xff_f7_f0, 0x5c_64_78, 0.34));
      const keyLight = new THREE.DirectionalLight(0xff_e7_d3, 1.34);
      keyLight.position.set(4.5, 10, 12);
      scene.add(keyLight);
      const fillLight = new THREE.DirectionalLight(0xb8_ce_ff, 0.32);
      fillLight.position.set(-7, 5, 8);
      scene.add(fillLight);
      const rimLight = new THREE.DirectionalLight(0xff_78_52, 0.5);
      rimLight.position.set(-5.5, 8.5, -7);
      scene.add(rimLight);
      scene.add(mesh);

      const bounds = new THREE.Box3().setFromObject(mesh);
      const size = bounds.getSize(new THREE.Vector3());
      const center = bounds.getCenter(new THREE.Vector3());
      // The old preview card gave the canvas only part of its total height.
      // Keep the character at roughly the same on-screen size now that the
      // transparent canvas occupies the whole stage.
      const halfHeight = size.y * 0.275;
      const cameraCenterX = center.x - size.y * 0.025;
      const cameraCenterY = bounds.max.y - size.y * 0.18;

      function resize() {
        if (!(canvas && renderer)) {
          return;
        }
        const width = Math.max(canvas.clientWidth, 1);
        const height = Math.max(canvas.clientHeight, 1);
        const halfWidth = halfHeight * (width / height);
        camera.left = -halfWidth;
        camera.right = halfWidth;
        camera.top = halfHeight;
        camera.bottom = -halfHeight;
        camera.position.set(cameraCenterX, cameraCenterY, center.z + 40);
        camera.lookAt(cameraCenterX, cameraCenterY, center.z);
        camera.updateProjectionMatrix();
        renderer.setSize(width, height, false);
      }

      const resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(canvas);
      resize();

      const adapter = new Adapter(mesh);
      adapterRef.current = adapter;
      adapter.reset(0);
      onStateChange("ready");

      function draw(now: number) {
        if (disposed || !(renderer && mesh)) {
          return;
        }
        const songAudio = songAudioRef.current;
        const songIsPlaying = Boolean(
          songAudio && !songAudio.paused && !songAudio.ended
        );
        for (const wristDecorationMaterial of wristDecorationMaterials) {
          wristDecorationMaterial.visible = !songIsPlaying;
        }
        const sample = songIsPlaying
          ? undefined
          : samplePerformanceRef.current();
        if (songIsPlaying && songAudio) {
          adapter.setSceneFrame({
            expressionPreset: "neutral",
            expressionWeight: 0,
            motion: "singing",
            mouthOpen: sampleSongMouth(songAudio.currentTime),
          });
        } else {
          adapter.setSceneFrame(undefined);
        }
        if (sample) {
          lastVoiceEpochRef.current = sample.position.epoch;
          adapter.apply(
            evaluateAudioFrame(sample.segment, sample.position),
            sample.position.epoch
          );
        } else if (
          ["FINISHED", "INTERRUPTED", "FAILED"].includes(voiceStateRef.current)
        ) {
          adapter.apply(
            {
              expressionPreset: "neutral",
              expressionWeight: 0,
              motion: "idle",
              mouthOpen: 0,
            },
            lastVoiceEpochRef.current
          );
        }
        adapter.update(now);
        renderer.render(scene, camera);
        animationFrame = requestAnimationFrame(draw);
      }
      animationFrame = requestAnimationFrame(draw);

      return () => resizeObserver.disconnect();
    }

    let disconnectResize: (() => void) | undefined;
    start()
      .then((cleanup) => {
        disconnectResize = cleanup;
      })
      .catch((error: unknown) => {
        console.error("MMD stage failed to load", error);
        onStateChange("error");
      });

    return () => {
      disposed = true;
      cancelAnimationFrame(animationFrame);
      disconnectResize?.();
      adapterRef.current?.destroy();
      adapterRef.current = null;
      if (mesh) {
        mesh.geometry.dispose();
        const materials = Array.isArray(mesh.material)
          ? mesh.material
          : [mesh.material];
        const textures = new Set<import("three").Texture>();
        for (const material of materials) {
          const texturedMaterial = material as import("three").Material & {
            envMap?: import("three").Texture;
            gradientMap?: import("three").Texture;
            map?: import("three").Texture;
          };
          for (const texture of [
            texturedMaterial.map,
            texturedMaterial.gradientMap,
            texturedMaterial.envMap,
          ]) {
            if (texture) {
              textures.add(texture);
            }
          }
          material.dispose();
        }
        for (const texture of textures) {
          texture.dispose();
        }
      }
      renderer?.dispose();
    };
  }, [onStateChange, stageEnabled]);

  if (!stageEnabled) {
    return null;
  }

  const isSpeaking = ["PLAYING", "BUFFERING"].includes(voiceState);

  return (
    <aside
      aria-label="Hanser Live2D 模型"
      className={`pointer-events-none fixed top-14 right-0 bottom-0 z-20 hidden w-[clamp(376px,calc(30vw+16px),476px)] bg-background lg:block ${
        isSinging ? "mmd-stage-singing" : isSpeaking ? "mmd-stage-speaking" : ""
      }`}
      data-testid="mmd-stage"
    >
      <div
        aria-hidden="true"
        className="absolute right-[12%] bottom-[4%] h-6 w-[66%] rounded-[50%] bg-black/[0.07] blur-md dark:bg-black/20"
      />
      <span
        aria-hidden="true"
        className="mmd-ambience-star absolute top-[18%] left-[18%] text-[9px]"
      >
        ✦
      </span>
      <span
        aria-hidden="true"
        className="mmd-ambience-star absolute top-[31%] right-[12%] text-[6px] [animation-delay:1.1s]"
      >
        •
      </span>
      <span
        aria-hidden="true"
        className="mmd-ambience-star absolute top-[43%] left-[9%] text-[7px] [animation-delay:2.2s]"
      >
        ✦
      </span>
      <div
        aria-hidden="true"
        className="absolute top-[27%] left-[7%] h-px w-20 rotate-[-12deg] border-t border-dashed border-[color:var(--hanser-gold)] opacity-[0.08]"
      />
      <canvas
        className="absolute right-4 bottom-0 h-[min(680px,calc(100dvh-4rem))] w-[clamp(360px,30vw,460px)] [filter:saturate(1.1)_contrast(1.025)_brightness(1.04)]"
        ref={canvasRef}
      />
    </aside>
  );
}
