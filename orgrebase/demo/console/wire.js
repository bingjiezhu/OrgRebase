(() => {
  "use strict";
  const scheme = "orgrebase.jcs-safe-number.v1";

  function scalarString(value) {
    for (let index = 0; index < value.length; index += 1) {
      const code = value.charCodeAt(index);
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = value.charCodeAt(++index);
        if (!(next >= 0xdc00 && next <= 0xdfff)) throw new Error("WIRE_UNICODE_INVALID");
      } else if (code >= 0xdc00 && code <= 0xdfff) throw new Error("WIRE_UNICODE_INVALID");
    }
    return JSON.stringify(value);
  }

  function canonical(value, depth = 0) {
    if (depth > 128) throw new Error("WIRE_DEPTH_EXCEEDED");
    if (value === null || typeof value === "boolean") return JSON.stringify(value);
    if (typeof value === "number") {
      if (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER) throw new Error("WIRE_NUMBER_OUT_OF_RANGE");
      return JSON.stringify(value);
    }
    if (typeof value === "string") return scalarString(value);
    if (Array.isArray(value)) return `[${Array.from(value, item => canonical(item, depth + 1)).join(",")}]`;
    if (typeof value === "object" && Object.prototype.toString.call(value) === "[object Object]") {
      return `{${Object.keys(value).sort().map(key => `${scalarString(key)}:${canonical(value[key], depth + 1)}`).join(",")}}`;
    }
    throw new Error("WIRE_JSON_VALUE_REQUIRED");
  }

  async function digest(value, selectedScheme = scheme) {
    if (selectedScheme !== scheme) throw new Error("WIRE_SCHEME_UNSUPPORTED");
    const bytes = new TextEncoder().encode(canonical(value));
    const hash = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));
    return `sha256:${Array.from(hash, byte => byte.toString(16).padStart(2, "0")).join("")}`;
  }

  async function verifyResponse(response) {
    const selectedScheme = response.headers.get("OrgRebase-Wire-Scheme");
    const expected = response.headers.get("OrgRebase-Wire-Digest");
    if (selectedScheme !== scheme || !/^sha256:[a-f0-9]{64}$/.test(expected || "")) throw new Error("WIRE_RESPONSE_PROTOCOL_INVALID");
    const value = await response.clone().json();
    if (await digest(value, selectedScheme) !== expected) throw new Error("WIRE_RESPONSE_DIGEST_MISMATCH");
  }

  globalThis.OrgRebaseWire = Object.freeze({ scheme, canonical, digest, verifyResponse });
})();
