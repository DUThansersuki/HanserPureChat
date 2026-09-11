"use client";

import {
  type MouseEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Button } from "@/components/ui/button";
import { useVoice } from "@/components/voice/voice-provider";
import {
  MMD_MOTION_LABELS,
  MMD_MOTION_NAMES,
  type MmdMotionName,
} from "@/lib/live2d/mmd-motion-library";
import type { MmdRigAdapter } from "@/lib/live2d/mmd-rig-adapter";
import { evaluateAudioFrame } from "@/lib/live2d/timeline-evaluator";

type StageState = "error" | "loading" | "ready";

export function MmdStage() {
  const { samplePerformance, state: voiceState } = useVoice();
  const samplePerformanceRef = useRef(samplePerformance);
  const voiceStateRef = useRef(voiceState);
  const lastVoiceEpochRef = useRef(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const adapterRef = useRef<MmdRigAdapter | null>(null);
  const [activeMotion, setActiveMotion] = useState<MmdMotionName>("idle");
  const [detail, setDetail] = useState("正在加载模型与贴图…");
  const [stageState, setStageState] = useState<StageState>("loading");

  samplePerformanceRef.current = samplePerformance;
  voiceStateRef.current = voiceState;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    let disposed = false;
    let animationFrame = 0;
    let renderer: import("three").WebGLRenderer | undefined;
    let composer:
      | import("three/addons/postprocessing/EffectComposer.js").EffectComposer
      | undefined;
    let chromaticPass:
      | import("three/addons/postprocessing/ShaderPass.js").ShaderPass
      | undefined;
    const postPasses: Array<{ dispose: () => void }> = [];
    let mesh: import("three").SkinnedMesh | undefined;

    async function start() {
      const THREE = await import("three");
      const [
        { MMDLoader },
        { EffectComposer },
        { RenderPass },
        { UnrealBloomPass },
        { ShaderPass },
        { OutputPass },
        { MmdRigAdapter: Adapter },
      ] = await Promise.all([
        import("three/addons/loaders/MMDLoader.js"),
        import("three/addons/postprocessing/EffectComposer.js"),
        import("three/addons/postprocessing/RenderPass.js"),
        import("three/addons/postprocessing/UnrealBloomPass.js"),
        import("three/addons/postprocessing/ShaderPass.js"),
        import("three/addons/postprocessing/OutputPass.js"),
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
            "/api/live2d/model/hanser_ver2.0.pmx",
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
      scene.background = new THREE.Color(0xb8_b5_b2);
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

      const postTarget = new THREE.WebGLRenderTarget(1, 1, {
        depthBuffer: true,
        format: THREE.RGBAFormat,
        stencilBuffer: false,
        type: THREE.UnsignedByteType,
      });
      postTarget.samples = 4;
      composer = new EffectComposer(renderer, postTarget);
      composer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      const renderPass = new RenderPass(scene, camera);
      composer.addPass(renderPass);
      postPasses.push(renderPass);

      const bloomPass = new UnrealBloomPass(
        new THREE.Vector2(1, 1),
        0.18,
        0.58,
        0.84
      );
      composer.addPass(bloomPass);
      postPasses.push(bloomPass);

      chromaticPass = new ShaderPass({
        fragmentShader: `
          uniform sampler2D tDiffuse;
          uniform vec2 resolution;
          uniform float amount;
          varying vec2 vUv;

          void main() {
            vec2 fromCenter = vUv - 0.5;
            float radial = smoothstep(0.12, 0.72, length(fromCenter));
            vec2 offset = fromCenter * amount * radial / resolution;
            vec4 center = texture2D(tDiffuse, vUv);
            float red = texture2D(tDiffuse, vUv + offset).r;
            float blue = texture2D(tDiffuse, vUv - offset).b;
            gl_FragColor = vec4(red, center.g, blue, center.a);
          }
        `,
        uniforms: {
          amount: { value: 1.1 },
          resolution: { value: new THREE.Vector2(1, 1) },
          tDiffuse: { value: null },
        },
        vertexShader: `
          varying vec2 vUv;

          void main() {
            vUv = uv;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }
        `,
      });
      composer.addPass(chromaticPass);
      postPasses.push(chromaticPass);
      const outputPass = new OutputPass();
      composer.addPass(outputPass);
      postPasses.push(outputPass);

      const bounds = new THREE.Box3().setFromObject(mesh);
      const size = bounds.getSize(new THREE.Vector3());
      const center = bounds.getCenter(new THREE.Vector3());
      const halfHeight = size.y * 0.2;
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
        composer?.setSize(width, height);
        chromaticPass?.uniforms.resolution.value.set(
          width * renderer.getPixelRatio(),
          height * renderer.getPixelRatio()
        );
      }

      const resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(canvas);
      resize();

      const adapter = new Adapter(mesh);
      let displayedMotion: MmdMotionName = "idle";
      adapterRef.current = adapter;
      adapter.reset(0);
      setStageState("ready");
      setDetail("循环待机｜挥手 · 歪头 · 比耶 Wink");

      function draw(now: number) {
        if (disposed || !(renderer && mesh)) {
          return;
        }
        const sample = samplePerformanceRef.current();
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
        const renderedMotion = adapter.getActiveMotion();
        if (renderedMotion !== displayedMotion) {
          displayedMotion = renderedMotion;
          setActiveMotion(renderedMotion);
        }
        composer?.render();
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
        setStageState("error");
        setDetail(error instanceof Error ? error.message : "模型加载失败");
      });

    return () => {
      disposed = true;
      cancelAnimationFrame(animationFrame);
      disconnectResize?.();
      adapterRef.current?.destroy();
      adapterRef.current = null;
      for (const pass of postPasses) {
        pass.dispose();
      }
      composer?.dispose();
      if (mesh) {
        mesh.geometry.dispose();
        const materials = Array.isArray(mesh.material)
          ? mesh.material
          : [mesh.material];
        for (const material of materials) {
          material.dispose();
        }
      }
      renderer?.dispose();
    };
  }, []);

  const play = useCallback((event: MouseEvent<HTMLButtonElement>) => {
    const motion = event.currentTarget.dataset.motion as MmdMotionName;
    adapterRef.current?.play(motion);
    setActiveMotion(motion);
  }, []);

  return (
    <aside className="fixed right-4 top-16 z-20 hidden h-[min(680px,calc(100dvh-5rem))] w-[460px] overflow-hidden rounded-2xl border border-border/60 bg-background/90 shadow-2xl backdrop-blur lg:flex lg:flex-col">
      <div className="flex items-center justify-between border-b border-border/50 px-4 py-3">
        <div>
          <p className="font-medium text-sm">Hanser 动作预览</p>
          <p className="text-muted-foreground text-xs">{detail}</p>
        </div>
        <span
          className={
            stageState === "ready"
              ? "text-emerald-600 text-xs"
              : stageState === "error"
                ? "text-red-500 text-xs"
                : "text-amber-600 text-xs"
          }
        >
          {stageState === "ready"
            ? "已就绪"
            : stageState === "error"
              ? "失败"
              : "加载中"}
        </span>
      </div>
      <div className="relative min-h-0 flex-1 overflow-hidden bg-[radial-gradient(circle_at_50%_38%,#f8f5f2_0%,#e8e6e5_58%,#d6d6d8_100%)] dark:bg-[radial-gradient(circle_at_50%_38%,#39383b_0%,#242429_62%,#17171b_100%)]">
        <canvas
          className="h-full w-full [filter:saturate(1.1)_contrast(1.025)_brightness(1.04)]"
          ref={canvasRef}
        />
      </div>
      <div className="grid grid-cols-2 gap-2 border-t border-border/50 p-3">
        {MMD_MOTION_NAMES.map((motion) => (
          <Button
            data-motion={motion}
            disabled={stageState !== "ready"}
            key={motion}
            onClick={play}
            size="sm"
            variant={activeMotion === motion ? "default" : "outline"}
          >
            {MMD_MOTION_LABELS[motion]}
          </Button>
        ))}
      </div>
    </aside>
  );
}
