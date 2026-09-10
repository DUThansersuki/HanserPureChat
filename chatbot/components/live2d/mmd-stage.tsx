"use client";

import {
  type MouseEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Button } from "@/components/ui/button";
import {
  MMD_MOTION_LABELS,
  MMD_MOTION_NAMES,
  type MmdMotionName,
} from "@/lib/live2d/mmd-motion-library";
import type { MmdRigAdapter } from "@/lib/live2d/mmd-rig-adapter";

type StageState = "error" | "loading" | "ready";

export function MmdStage() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const adapterRef = useRef<MmdRigAdapter | null>(null);
  const [activeMotion, setActiveMotion] = useState<MmdMotionName>("idle");
  const [detail, setDetail] = useState("正在加载模型与贴图…");
  const [stageState, setStageState] = useState<StageState>("loading");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    let disposed = false;
    let animationFrame = 0;
    let renderer: import("three").WebGLRenderer | undefined;
    let mesh: import("three").SkinnedMesh | undefined;

    async function start() {
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

      const scene = new THREE.Scene();
      const camera = new THREE.OrthographicCamera(-5, 5, 8, -8, 0.1, 200);
      renderer = new THREE.WebGLRenderer({
        alpha: true,
        antialias: true,
        canvas,
        powerPreference: "high-performance",
      });
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.setClearColor(0x00_00_00, 0);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));

      scene.add(new THREE.AmbientLight(0xff_ff_ff, 0.85));
      const keyLight = new THREE.DirectionalLight(0xff_ff_ff, 0.75);
      keyLight.position.set(4, 10, 12);
      scene.add(keyLight);
      scene.add(mesh);

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
      }

      const resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(canvas);
      resize();

      const adapter = new Adapter(mesh);
      adapterRef.current = adapter;
      adapter.reset(0);
      setStageState("ready");
      setDetail("无物理模式｜4 个候选动作");

      function draw(now: number) {
        if (disposed || !(renderer && mesh)) {
          return;
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
        setStageState("error");
        setDetail(error instanceof Error ? error.message : "模型加载失败");
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
      <div className="relative min-h-0 flex-1 overflow-hidden bg-[linear-gradient(45deg,hsl(var(--muted))_25%,transparent_25%),linear-gradient(-45deg,hsl(var(--muted))_25%,transparent_25%),linear-gradient(45deg,transparent_75%,hsl(var(--muted))_75%),linear-gradient(-45deg,transparent_75%,hsl(var(--muted))_75%)] bg-[length:24px_24px] bg-[position:0_0,0_12px,12px_-12px,-12px_0]">
        <canvas className="h-full w-full" ref={canvasRef} />
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
