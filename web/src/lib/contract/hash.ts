// SHA-256 over exact bytes, portable across the browser and Node: both provide
// crypto.subtle (the web view and vitest's node runtime alike). The placement result
// reports stats.input_sha256 over the scene bytes "as uploaded" (server/scene.py hashes
// the exact payload it parsed), so the client hashes the very bytes it will send — not a
// re-serialization — and association compares the two digests.

const encoder = new TextEncoder();

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    bytes as unknown as ArrayBufferView<ArrayBuffer>,
  );
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Convenience for hashing a JSON text as UTF-8. */
export async function sha256HexOfText(text: string): Promise<string> {
  return sha256Hex(encoder.encode(text));
}
