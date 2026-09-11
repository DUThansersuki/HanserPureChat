import { readFile } from "node:fs/promises";
import path from "node:path";

const MODEL_ROOT =
  process.env.HANSER_MMD_MODEL_ROOT ??
  path.resolve(
    process.cwd(),
    "..",
    ".runtime",
    "mmd",
    "hanser_v2.0_cloth2_test"
  );

const ASSET_PATHS = new Map([
  ["hanser_ver2.0.pmx", "hanser_ver2.0.pmx"],
  ["new/cloth1_basecolor.png", "new/cloth1_BaseColor.png"],
  ["tex/body.png", "tex/body.png"],
  ["tex/cloth1.png", "tex/cloth1.png"],
  ["tex/cloth2.png", "tex/cloth2.png"],
  ["tex/eyes.png", "tex/eyes.png"],
  ["tex/face.png", "tex/face.png"],
  ["tex/fitting.png", "tex/fitting.png"],
  ["tex/hair.png", "tex/hair.png"],
]);

const CONTENT_TYPES: Record<string, string> = {
  ".pmx": "application/octet-stream",
  ".png": "image/png",
};

export async function GET(
  _request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  const requestedPath = (await context.params).path.join("/").toLowerCase();
  const assetPath = ASSET_PATHS.get(requestedPath);
  if (!assetPath) {
    return new Response("Not found", { status: 404 });
  }

  try {
    const file = await readFile(path.join(MODEL_ROOT, assetPath));
    return new Response(new Uint8Array(file), {
      headers: {
        "Cache-Control": "private, max-age=3600",
        "Content-Type":
          CONTENT_TYPES[path.extname(assetPath)] ?? "application/octet-stream",
      },
    });
  } catch {
    return new Response(
      "MMD candidate is not prepared. Run scripts/prepare_mmd_candidate.ps1.",
      { status: 503 }
    );
  }
}
