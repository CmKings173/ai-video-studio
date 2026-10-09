import { timingSafeEqual } from "node:crypto";
import { isIP } from "node:net";

/** Only an ingress possessing the server-only credential may supply an address. */
export function forwardClientIdentity(incoming: Headers, outgoing: Headers): void {
  const ingressToken = process.env.STUDIO_INGRESS_TOKEN || "";
  const proxyToken = process.env.INTERNAL_PROXY_TOKEN || "";
  if (process.env.APP_ENV?.toLowerCase() === "production" && (
    ingressToken.length < 32 || proxyToken.length < 32 || ingressToken === proxyToken
    || !/^[\x20-\x7e]+$/.test(ingressToken) || !/^[\x20-\x7e]+$/.test(proxyToken)
    || ingressToken.startsWith("replace-with") || proxyToken.startsWith("replace-with")
  )) {
    throw new Error("Production trusted ingress credentials are missing or invalid");
  }
  const supplied = incoming.get("x-studio-ingress-token") || "";
  const address = incoming.get("x-studio-client-ip") || "";
  if (ingressToken.length < 32 || proxyToken.length < 32 || !isIP(address) || address.includes("%")) return;
  const expected = Buffer.from(ingressToken);
  const actual = Buffer.from(supplied);
  if (expected.length !== actual.length || !timingSafeEqual(expected, actual)) return;
  outgoing.set("x-studio-client-ip", address);
  outgoing.set("x-studio-proxy-token", proxyToken);
}
