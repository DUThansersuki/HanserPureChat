import { createReadStream, statSync } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";

const root = path.resolve(process.argv[2]);
const port = Number(process.argv[3] ?? "18765");

createServer((request, response) => {
  const name = path.basename(decodeURIComponent(new URL(request.url, "http://localhost").pathname));
  const filePath = path.join(root, name);
  const size = statSync(filePath).size;
  const range = request.headers.range?.match(/^bytes=(\d+)-(\d*)$/);
  const start = range ? Number(range[1]) : 0;
  const end = range?.[2] ? Number(range[2]) : size - 1;
  if (start >= size || end >= size || start > end) {
    response.writeHead(416, { "Content-Range": `bytes */${size}` });
    response.end();
    return;
  }
  const headers = {
    "Accept-Ranges": "bytes",
    "Content-Length": end - start + 1,
    "Content-Type": name.endsWith(".json") ? "application/json" : "application/zip",
  };
  if (range) {
    headers["Content-Range"] = `bytes ${start}-${end}/${size}`;
  }
  response.writeHead(range ? 206 : 200, headers);
  createReadStream(filePath, { start, end }).pipe(response);
}).listen(port, "127.0.0.1", () => {
  process.stdout.write(`Serving ${root} at http://127.0.0.1:${port}\n`);
});
